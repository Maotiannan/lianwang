"""Keep Clash in charge of destination rules, including flows diverted by macOS."""
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import subprocess

PREFIX = 'net-egress-menu-'
CORE_NAME = 'macos-system-proxy'
DIRECT = 'DIRECT'


def key(text):
    return hashlib.sha256(text.encode()).hexdigest()[:20]


def endpoint(mixed_port):
    output = subprocess.check_output(['/usr/sbin/scutil', '--proxy'], text=True, timeout=4)
    values = dict(re.findall(r'^  (\w+) : ([^\n]+)$', output, re.M))
    host = values.get('SOCKSProxy', '')
    port = int(values.get('SOCKSPort') or 0)
    if not (values.get('SOCKSEnable') == '1' and host in ('localhost','127.0.0.1','::1') and port == mixed_port):
        raise ValueError('全机分流需要 macOS 的 SOCKS 系统代理指向 Clash Verge 当前的混合端口。')
    bypass=[]
    for value in re.findall(r'^    \d+ : (\S+)$',output,re.M):
        try:
            bypass.append({"kind":"ipCIDR","value":str(ipaddress.ip_network(value,strict=False))})
        except ValueError:
            if value.startswith("*."):
                bypass.append({"kind":"domainSuffix","value":value[2:]})
    # macOS may attribute a DNS lookup to the requesting app. Its LAN resolver
    # must remain reachable before Xray or Clash can resolve an upstream.
    dns = subprocess.check_output(['/usr/sbin/scutil','--dns'], text=True, timeout=4)
    local_networks = set()
    for value in re.findall(r'^\s*nameserver\[\d+\] : (\S+)', dns, re.M):
        try:
            address = ipaddress.ip_address(value.split('%',1)[0])
        except ValueError:
            continue
        if address.is_private or address.is_link_local:
            local_networks.add(str(ipaddress.ip_network(address)))
    return {'host':'127.0.0.1' if host == 'localhost' else host, 'port':port,
            'resolverNetworks':sorted(local_networks),'bypassMatchers':bypass}


def source_matchers(kind, value, inspect_app):
    if kind == 'PROCESS-NAME':
        if re.fullmatch(r'[\w. -]+', value):
            return [{'kind':'application','value':value}]
        # Some subscriptions contain Windows/percent-escaped names. Use an exact
        # executable path for punctuation the extension's name matcher cannot parse.
        paths = [Path(base) / value for base in ('/usr/bin','/usr/local/bin','/opt/homebrew/bin')]
        for base in ('/Applications','/System/Applications',str(Path.home()/'Applications')):
            paths.append(Path(base)/(value+'.app')/'Contents/MacOS'/value)
        return [{'kind':'processPath','value':str(p)} for p in paths]
    if kind == 'PROCESS-PATH' and value.startswith('/'):
        return [{'kind':'processPath','value':value}]
    if kind == 'PROCESS-PATH-REGEX':
        match = re.fullmatch(r'\^(/[^*+?(){}|]+?)(?:\[\.\]|\\\.)app/Contents/\.\*\$?', value)
        if match:
            app = Path(match[1].replace(r'\.', '.').replace('[.]','.').replace(r'\ ', ' ') + '.app')
            if app.is_dir():
                data = inspect_app(str(app), DIRECT)
                result=[{'kind':'processPath','value':p} for p in data['executables']]
                ids=set(data.get('signingIDs', ())) | ({data['bundleID']} if data.get('bundleID') else set())
                return result+[{'kind':'application','value':s} for s in sorted(ids)]
    return []


def inherited(rules, inspect_app):
    blocks = []
    unsupported = 0
    previous = None
    for raw in rules:
        if raw.startswith(('PROCESS-PATH-REGEX,^(?<EgressMenu>', 'IN-NAME,' + PREFIX)):
            continue
        fields = raw.split(',')
        if len(fields) != 3 or fields[0] not in ('PROCESS-NAME','PROCESS-PATH','PROCESS-PATH-REGEX'):
            previous = None
            continue
        source = source_matchers(fields[0], fields[1], inspect_app)
        if not source:
            unsupported += 1
            previous = None
            continue
        if not previous or previous['choice'] != fields[2]:
            previous = {'choice':fields[2], 'rules':[], 'matchers':[]}
            blocks.append(previous)
        previous['rules'].append(raw)
        for matcher in source:
            if matcher not in previous['matchers']:
                previous['matchers'].append(matcher)
    for block in blocks:
        block['key'] = key('process-rules/' + '\n'.join(block['rules']))
    return blocks, unsupported


def prepare(state, config, mixed_port, inspect_app, free_port, import_profile):
    system = endpoint(mixed_port)
    blocks, unsupported = inherited(config.get('rules', ()), inspect_app)
    pool = state.setdefault('identityRelays', {})
    used = {v['port'] for v in (*state['targets'].values(), *pool.values())} | {state['corePort']}
    def allocate(name):
        port = free_port(used)
        used.add(port)
        return {'key':name,'port':port}
    if DIRECT not in state['targets']:
        state['targets'][DIRECT] = dict(allocate(key(DIRECT)), kind='direct')
    for block in blocks:
        if block['key'] not in pool:
            pool[block['key']] = allocate(block['key'])
    relays = [state['targets'][DIRECT]] + [pool[b['key']] for b in blocks]
    nodes = [{'name':CORE_NAME,'type':'socks5','server':system['host'],'port':system['port'],'udp':True}]
    nodes += [{'name':'relay-'+r['key'],'type':'socks5','server':'127.0.0.1','port':r['port'],
               'username':'net-egress','password':state['token'],'udp':True} for r in relays]
    fingerprint = key(json.dumps(nodes,sort_keys=True))
    profiles = state.setdefault('systemProfiles', {})
    if fingerprint not in profiles:
        profiles[fingerprint] = import_profile('NetworkEgress-System-'+fingerprint+'.yaml', {
            'proxies':nodes, 'proxy-groups':[{'name':'System','type':'select','proxies':[CORE_NAME]}], 'rules':['MATCH,System']})
    state['systemPolicy'] = {**system,'profileID':profiles[fingerprint],'blocks':blocks,'unsupported':unsupported}
    state['policyVersion'] = 4
