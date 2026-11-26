"""Local routing integration for the macOS menu bar app. Requires Python 3.9+."""
import base64
import contextlib
import copy
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlsplit
import workspace
import observation
import system_route
import protection
import simple_mode
import engine
import menu_visibility
import policy_transport


SUPPORT = Path.home() / "Library/Application Support"
CV = SUPPORT / "io.github.clash-verge-rev.clash-verge-rev"
APP_DATA = SUPPORT / "NetworkEgress"
STATE = APP_DATA / "settings.json"
JOURNAL = APP_DATA / "pending.json"
PAUSED = APP_DATA / "paused"
HOOK = CV / "profiles/Script.js"
CONFIG = CV / "clash-verge.yaml"
SOCKET = "/var/run/clash-verge-service/users/%d/verge-mihomo.sock" % os.getuid()
CTL = engine.CTL
YQ = "/opt/homebrew/bin/yq" if Path("/opt/homebrew/bin/yq").is_file() else "/usr/local/bin/yq"
BEGIN = "// BEGIN local.network-egress controlled rules"
END = "// END local.network-egress controlled rules"
REGEX_PREFIX = "PROCESS-PATH-REGEX,^(?<EgressMenu>"
LISTENER_PREFIX = "net-egress-menu-"
ROUTING_VERSION = 3
RESOURCE = Path(__file__).resolve().parent
RESERVED = {"DIRECT", "GLOBAL", "REJECT", "REJECT-DROP", "PASS", "PASS-RULE", "COMPATIBLE", "DNS", "MATCH"}


class Problem(Exception):
    pass


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def read_json(path):
    return json.loads(path.read_text())


def atomic(path, data, mode=0o600):
    fd, temporary = tempfile.mkstemp(prefix=".egress-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_json(path, value):
    atomic(path, compact(value).encode())


@contextlib.contextmanager
def locked():
    APP_DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (APP_DATA / "update.lock").open("a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield


def command(argv, data=None, timeout=15):
    return subprocess.run(argv, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def mc(method, params=None, interaction=False, timeout=18):
    engine.ensure_running(CTL)
    args = [CTL, method, "--no-launch", "--timeout", str(timeout)]
    if params is not None:
        args.append("--params-stdin")
    if interaction:
        args.append("--allow-interaction")
    p = command(args, compact(params).encode() if params is not None else None, timeout+6)
    try:
        response = json.loads(p.stdout or p.stderr)
    except ValueError:
        raise Problem("MClash 未响应。请检查应用是否正在运行。")
    if "result" not in response or p.returncode:
        info = response.get("error") or response
        raise Problem("%s · %s" % (method, info.get("message", "MClash 请求失败")[:260]))
    return response["result"]


class Local(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(SOCKET)


def clash(path, method="GET", body=None, timeout=8):
    con = Local("localhost", timeout=timeout)
    try:
        con.request(method, path, body=compact(body).encode() if body is not None else None,
                    headers={"Content-Type": "application/json"})
        response = con.getresponse()
        raw = response.read()
        if response.status >= 300:
            # A YAML parse error can echo a complete upstream configuration. Keep it local.
            raise Problem("Clash Verge %s 返回 HTTP %d。" % (path.split("?")[0], response.status))
        return json.loads(raw) if raw else None
    finally:
        con.close()


def yaml_config():
    res = command([YQ, "-o=json", ".", str(CONFIG)])
    if res.returncode:
        raise Problem("读取 Clash Verge 当前配置失败。")
    return json.loads(res.stdout)


def choices(conf):
    options = {}
    for field, kind in (("proxies", "node"), ("proxy-groups", "group")):
        for entry in conf.get(field) or []:
            name = entry.get("name")
            if isinstance(name, str) and name and name not in RESERVED and not any(c in name for c in ",\r\n"):
                options[name] = kind
    return options


def available_port(used=()):
    for _ in range(100):
        with socket.socket() as tcp, socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
            tcp.bind(("127.0.0.1", 0))
            port = tcp.getsockname()[1]
            if port in used:
                continue
            try:
                udp.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise Problem("没有可用的本机转发端口。")


def new_state():
    return {"schema":1, "token":secrets.token_urlsafe(32), "profileID":None,
            "corePort":available_port(), "targets":{}, "overrides":{}, "protectServices":True}


def set_up_targets(state, conf):
    used = {t["port"] for t in state["targets"].values()} | {state["corePort"]}
    for name, kind in choices(conf).items():
        if name not in state["targets"]:
            port = available_port(used)
            state["targets"][name] = {"key":hashlib.sha256(name.encode()).hexdigest()[:20], "port":port,"kind":kind}
            used.add(port)


def m_profile(state):
    proxies, groups = [], []
    for p in state["targets"].values():
        proxy_name = "relay-" + p["key"]
        proxies.append({"name":proxy_name,"type":"socks5","server":"127.0.0.1","port":p["port"],
                        "username":"net-egress","password":state["token"],"udp":True})
        groups.append({"name":"eg-"+p["key"],"type":"select","proxies":[proxy_name]})
    return {"mixed-port":state["corePort"],"allow-lan":False,"bind-address":"127.0.0.1",
            "mode":"rule","ipv6":True,"tun":{"enable":False},"proxies":proxies,
            "proxy-groups":groups,"rules":["MATCH,DIRECT"]}


def import_profile(filename, document):
    converted=command([YQ,"-p=json","-o=yaml","-P",".","-"],compact(document).encode())
    if converted.returncode: raise Problem("生成 MClash 本机转发配置失败。")
    data=base64.b64encode(converted.stdout).decode()
    return mc("profiles.import",{"fileName":filename,"dataBase64":data,"activate":False})["id"]


def import_relays(state,filename):
    return import_profile(filename, m_profile(state))


def setup_capture():
    status = mc("appRouting.status")
    if status.get("state") == "on" or status.get("state") == "requiresReboot":
        return status
    if not status.get("enabled"):
        return mc("appRouting.setEnabled", {"enabled":True}, interaction=True, timeout=55)
    return mc("appRouting.retry", interaction=True, timeout=55)


def setup():
    with locked():
        state=read_json(STATE) if STATE.exists() else new_state()
        if JOURNAL.exists():
            raise Problem("上次分流修改尚待恢复，请先运行 recover。")
        if state.get("systemPolicy") and state.get("policyProviderMode") and not state.get("setupPending"):
            if mc("configuration.snapshot", {"nodeLimit":1})["currentWorkspaceID"] != workspace.WORKSPACE:
                raise Problem("MClash 当前在使用其他工作区。")
            return setup_capture()
        config = clash("/configs")
        if config.get("mode") != "rule" or (config.get("tun") or {}).get("enable"):
            raise Problem("首次启用需要 Clash Verge 使用规则模式，并关闭 TUN。")
        system_route.endpoint(config["mixed-port"])
        if not HOOK.is_file():
            raise Problem("找不到 Clash Verge 全局扩展脚本，请先在 Clash Verge 启用订阅。")
        state["setupPending"] = True
        conf=yaml_config()
        if not state["targets"]:
            set_up_targets(state,conf)
        if not state["targets"]:
            raise Problem("Clash Verge 没有可用出口。")
        save_json(STATE,state)
        listed=mc("profiles.list")["items"]
        if state["profileID"] is None:
            if listed:
                adopted=[p for p in listed if p.get("name")=="NetworkEgress-Local"]
                if len(adopted)!=1:
                    raise Problem("MClash 已有其他配置，需先确认使用的配置。")
                state["profileID"]=adopted[0]["id"]
            else:
                state["profileID"]=import_relays(state,"NetworkEgress-Local.yaml")
            save_json(STATE,state)
        core=mc("core.status")
        if core.get("activeProfileID") not in (None,state["profileID"]):
            raise Problem("MClash 正在使用其他配置，请切回 NetworkEgress-Local。")
        mc("settings.patch",{"autoEnableSystemProxy":False,"closeConnectionsOnRoutingChange":False,
                             "lightweightMode":True,"autoConnectOnLaunch":True,"launchAtLogin":False})
        mc("app.update.configure",{"automaticChecks":False,"automaticDownloads":False})
        mc("app.ui.hide")
        if core.get("activeProfileID")!=state["profileID"]:
            mc("profiles.activate",{"id":state["profileID"]})
        system_route.prepare(state,conf,clash("/configs")["mixed-port"],inspect_target,available_port,import_profile)
        prepare_policy(state)
        state["configGeneration"] = CONFIG.stat().st_mtime_ns
        state["routingVersion"] = ROUTING_VERSION
        save_json(STATE,state)
        update_cv(state)
        workspace.apply(mc,state,allow_activation=True)
        if not mc("core.status")["connected"]:
            mc("core.connect",timeout=45)
        status = setup_capture()
        state.pop("setupPending")
        save_json(STATE,state)
        return status


def signing_ids(executables, bundle=None):
    ids=set()
    for exe in executables:
        result=command(["/usr/bin/codesign","-dv",exe], timeout=5)
        match=re.search(rb"(?m)^Identifier=([^\r\n]+)$",result.stderr)
        if not match: continue
        name=match[1].decode(errors="replace")
        if not re.fullmatch(r"[\w.-]{1,255}",name) or "." not in name: continue
        if not bundle or name==bundle or name.startswith((bundle+".",bundle+"-")):
            ids.add(name)
    return sorted(ids)


def inspect_target(path, choice):
    if not os.path.isabs(path) or "," in path or any(c in path for c in "\n\r\x00"):
        raise Problem("该路径无法用 Clash 的进程规则表示。")
    target=Path(path)
    if engine.is_router(path) or target.name in ("MClash.app","Clash Verge.app","联网.app","网络出口.app"):
        raise Problem("代理引擎自身的连接用于转发其他应用，给它指定出口会造成循环。")
    if target.is_file() and os.access(path,os.X_OK):
        exes=[str(target.resolve())]
        return {"choice":choice,"executables":exes,"bundleID":None,"signingIDs":signing_ids(exes)}
    if not (target.is_dir() and target.suffix.lower()==".app"):
        raise Problem("请选择应用程序或可执行文件。")
    bundle_info=observation.info(path)
    main=target/"Contents/MacOS"/bundle_info.get("CFBundleExecutable","")
    if not main.is_file(): raise Problem("应用缺少有效的主程序。")
    exes={str(main.resolve())}
    for directory, dirs, files in os.walk(target/"Contents",followlinks=False):
        # Localized folders and unrelated static assets cannot be executables.
        dirs[:]=[d for d in dirs if not d.endswith((".lproj",".dSYM"))]
        for f in files:
            p=Path(directory)/f
            if not os.access(p,os.X_OK) or p.is_symlink(): continue
            try:
                with p.open("rb") as stream: sig=stream.read(4).hex()
            except OSError: continue
            if sig in {"cffaedfe","cefaedfe","feedface","feedfacf","cafebabe","cafebabf","bebafeca"}:
                exes.add(str(p.resolve()))
    # Dylibs and framework binaries are never launched as helpers.
    exes={s for s in exes if not s.endswith((".dylib",".so")) and not re.search(r"\.framework/(?:Versions/[^/]+/)?[^/]+$",s)}
    bundle=bundle_info.get("CFBundleIdentifier")
    return {"choice":choice,"executables":sorted(exes),"bundleID":bundle,"signingIDs":signing_ids(exes,bundle)}


def app_pattern(path):
    # Escape only Go RE2 metacharacters. Python's re.escape also escapes spaces.
    text=re.sub(r"([\\.^$|?*+(){}\[\]])",r"\\\1",path)
    return text+("/.*" if path.lower().endswith(".app") else "$")


def entries(state):
    result=[]
    for path,o in sorted(state["overrides"].items()):
        if state.get("protectServices") and protection.is_service(path): continue
        choice=o["choice"]
        # Older versions stored a group name for the simple "proxy" button.
        # Keep that key as a transport identity, but follow the active profile.
        out={"choice":choice,"pattern":app_pattern(path),
             "followSubscription":choice!="DIRECT" and o.get("followSubscription",True)}
        if choice!="DIRECT" or state.get("systemPolicy"):
            out.update(endpoint=state["targets"][choice],password=state["token"])
        result.append(out)
    active_choices = {o['choice'] for o in state['overrides'].values()}
    for choice, follow in sorted(state.get('retainedRelayChoices', {}).items()):
        if choice not in active_choices and choice in state['targets']:
            result.append({'role': 'relay', 'choice': choice, 'followSubscription': follow,
                           'endpoint': state['targets'][choice], 'password': state['token']})
    for block in (state.get("systemPolicy") or {}).get("blocks", []):
        result.append({"role":"inherited","rules":block["rules"],"endpoint":state["identityRelays"][block["key"]],
                       "password":state["token"]})
    if state.get('policyProviderMode'):
        providers = {}
        for name, choice, follow in [('direct', 'DIRECT', False), ('proxy', state['proxyRelayChoice'], True)]:
            providers[name] = {'name': LISTENER_PREFIX+'policy-'+name,
                'path': str(HOOK.parent/('network-egress-policy-'+name+'.yaml')),
                'choice': choice, 'followSubscription': follow, 'payload': []}
        for row in result:
            if row.get('role') or not row.get('pattern'):
                continue
            name = ('direct' if row['choice']=='DIRECT' else 'proxy' if row.get('followSubscription')
                    else 'fixed-'+hashlib.sha256(row['choice'].encode()).hexdigest()[:20])
            if name not in providers:
                providers[name] = {'name': LISTENER_PREFIX+'policy-'+name,
                    'path': str(HOOK.parent/('network-egress-policy-'+name+'.yaml')),
                    'choice': row['choice'], 'followSubscription': False, 'payload': []}
            providers[name]['payload'].append('PROCESS-PATH-REGEX,^(?<EgressMenu>'+row['pattern']+')')
        transport_key = hashlib.sha256(('policy:'+state['token']).encode()).hexdigest()
        for provider in providers.values():
            provider['url'] = 'http://127.0.0.1:%d/%s/%s.yaml' % (
                state['policyPort'], transport_key, provider['name'])
        result.append({'role': 'policy', 'providers': list(providers.values())})
    return result


def retain_relay_choices(after, before):
    """Keep shared SOCKS listeners alive while established sessions drain."""
    pool = after.setdefault('retainedRelayChoices', {})
    pool.update(before.get('retainedRelayChoices', {}))
    for state in (before, after):
        for override in state.get('overrides', {}).values():
            choice = override['choice']
            if choice in after.get('targets', {}):
                pool[choice] = choice != 'DIRECT' and override.get('followSubscription', True)


def prepare_policy(state):
    """Reserve stable relays and a loopback port for temporary rule delivery."""
    choice = state.get('proxyRelayChoice')
    if choice not in state['targets'] or choice == 'DIRECT':
        choice = next((v['choice'] for v in state['overrides'].values()
                       if v['choice']!='DIRECT' and v.get('followSubscription', True)), None)
    if choice not in state['targets']:
        choice = next((k for k,v in state['targets'].items() if v.get('kind')=='group'), None)
    if choice is None:
        choice = next((k for k,v in state['targets'].items() if v.get('kind')=='node'), None)
    if choice is None:
        raise Problem('尚未准备可热更新的代理入口，已保留现有网络。')
    state['proxyRelayChoice'] = choice
    if not state.get('policyPort'):
        used = {target['port'] for target in state['targets'].values()}
        used.update(target['port'] for target in state.get('identityRelays', {}).values())
        used.update([state.get('corePort'), (state.get('systemPolicy') or {}).get('port')])
        state['policyPort'] = available_port(used)
    state['policyProviderMode'] = True
    retain_relay_choices(state, state)
    state['retainedRelayChoices'].update({'DIRECT': False, choice: True})


def enhanced_script(original,rows):
    if original.count(BEGIN)!=original.count(END) or original.count(BEGIN)>1:
        raise Problem("Clash 的全局扩展脚本有未完成的修改。")
    if BEGIN in original:
        a=original.index(BEGIN); b=original.index(END,a)+len(END)
        if original[a-1:a]=="\n":a-=1
        if original[b:b+1]=="\n":b+=1
        original=original[:a]+original[b:]
    if not rows: return original
    # Save the existing enhancer verbatim, then wrap it at the entry point.
    js=(RESOURCE/"routing.js").read_text()
    return (original+"\n"+BEGIN+"\n"+js+"\nconst networkEgressUserMain = main;\n"
            "main = function(config, profileName) {\n"
            "  return applyNetworkEgress(networkEgressUserMain(config, profileName), "
            +compact(rows)+");\n};\n"+END+"\n")


def same_loaded_rule(raw, actual):
    """Compare config rules with Mihomo's canonical API representation."""
    fields = raw.split(',')
    if fields[-1] == 'no-resolve':
        fields = fields[:-1]
    kind = fields[0].replace('-', '').casefold()
    if kind == 'ipcidr6':
        kind = 'ipcidr'
    payload = ','.join(fields[1:-1])
    loaded_payload = actual['payload']
    if kind == 'geoip':
        payload, loaded_payload = payload.casefold(), loaded_payload.casefold()
    return (kind == actual['type'].casefold() and fields[-1] == actual['proxy']
            and payload == loaded_payload)


def policy_content(provider):
    # Mihomo's fast YAML parser requires a literal payload: header.
    rules = provider['payload'] or ['PROCESS-PATH-REGEX,^$a']
    return ('payload:\n'+''.join('  - '+compact(rule)+'\n' for rule in rules)).encode()


def update_cv(state, live_only=False, force_providers=False):
    rows=entries(state)
    config_generation=CONFIG.stat().st_mtime_ns
    conf=yaml_config()
    from_disk=dict(choices(conf))
    before=clash("/configs")
    # Synchronize rules in any mode, preserving the user's current mode.
    # This allows repairing a profile while global mode temporarily bypasses it.
    live_names=clash("/proxies")["proxies"]
    if not set(from_disk).issubset(live_names):
        raise Problem("Clash 正在切换订阅，请稍后再试。")
    # Preserve every unrelated runtime rule. This also catches a profile switch mid-save.
    start_rules=clash("/rules")["rules"]
    existing=[r for r in start_rules if not ((r.get("type")=="ProcessPathRegex" and
                    r.get("payload","").startswith("^(?<EgressMenu>")) or
                    (r.get("type") in ("InName", "RuleSet") and r.get("payload","").startswith(LISTENER_PREFIX)))]
    managed_prefixes = (REGEX_PREFIX, 'IN-NAME,'+LISTENER_PREFIX, 'RULE-SET,'+LISTENER_PREFIX)
    base_rules=[r for r in conf.get("rules",[]) if not r.startswith(managed_prefixes)]
    if len(existing)!=len(base_rules):
        raise Problem("磁盘订阅和 Clash 运行中的规则不同，已保留现有配置。请在 Clash 重新激活订阅后重试。")
    hook=HOOK.read_text()
    candidate=enhanced_script(hook,rows)
    js=(RESOURCE/"routing.js").read_text()
    # Run only our pure transform. The user's existing enhancer has already run on CONFIG.
    runner=js+"\nconst [base, rows] = JSON.parse(require('fs').readFileSync(0,'utf8'));\nprocess.stdout.write(JSON.stringify(applyNetworkEgress(base,rows)));"
    node=next((p for p in ["/opt/homebrew/bin/node","/usr/local/bin/node"] if os.access(p,os.X_OK)),None)
    if node is None: raise Problem("找不到本机 Node.js。")
    result=command([node,"-e",runner],compact([conf,rows]).encode())
    if result.returncode: raise Problem("生成 Clash 分流规则失败。")
    live=json.loads(result.stdout)
    # Preserve current runtime flags, including the user's TUN preference and port.
    for key in ("mode","mixed-port","port","socks-port","redir-port","tproxy-port","allow-lan","bind-address",
                "log-level","ipv6","find-process-mode"):
        if key in before: live[key]=before[key]
    if "tun" in before:
        live.setdefault("tun",{})["enable"]=before["tun"]["enable"]
    expected_rules=[r for r in live["rules"] if r.startswith(REGEX_PREFIX)]
    active=[p for p in state["overrides"] if not (state.get("protectServices") and protection.is_service(p))]
    policy = next((r for r in rows if r.get('role')=='policy'), None)
    if policy:
        if sum(len(p['payload']) for p in policy['providers']) != len(active):
            raise Problem('生成的应用规则数不正确。')
    elif len({r.split(",")[1] for r in expected_rules})!=len(active): raise Problem("生成的规则数不正确。")
    if HOOK.read_text()!=hook or CONFIG.stat().st_mtime_ns!=config_generation:
        raise Problem("Clash 配置刚发生变化，请重试。")
    selected={n:p["now"] for n,p in live_names.items() if p.get("type")=="Selector" and p.get("now")}
    expected_managed=[r for r in live["rules"] if r.startswith(managed_prefixes)]
    active_managed=[]
    for rule in start_rules:
        if rule["type"]=="ProcessPathRegex" and rule["payload"].startswith("^(?<EgressMenu>"):
            active_managed.append("PROCESS-PATH-REGEX,"+rule["payload"]+","+rule["proxy"])
        elif rule["type"]=="InName" and rule["payload"].startswith(LISTENER_PREFIX):
            active_managed.append("IN-NAME,"+rule["payload"]+","+rule["proxy"])
        elif rule['type']=='RuleSet' and rule['payload'].startswith(LISTENER_PREFIX):
            active_managed.append('RULE-SET,'+rule['payload']+','+rule['proxy'])
    ports={l["port"] for l in live.get("listeners",[]) if str(l.get("name","")).startswith(LISTENER_PREFIX)}
    base_matches=all(same_loaded_rule(raw,actual) for raw,actual in zip(base_rules,existing))
    same_configuration = active_managed==expected_managed and base_matches
    if live_only and not same_configuration:
        raise Problem('本次变更需要重新加载共享配置，已取消切换并保留其他应用连接。')
    with contextlib.ExitStack() as transaction:
        server = None
        if policy:
            documents = {urlsplit(p['url']).path: policy_content(p) for p in policy['providers']}
            try:
                server = transaction.enter_context(policy_transport.serve(state['policyPort'], documents))
            except OSError:
                raise Problem('本机规则传送端口被占用，已保留现有连接。') from None
        if candidate!=hook:
            perm=0o600 if rows else state.get("hookMode",stat.S_IMODE(HOOK.stat().st_mode))
            atomic(HOOK,candidate.encode(),perm)
        changed_providers = []
        if policy:
            for provider in policy['providers']:
                path = Path(provider['path'])
                content = policy_content(provider)
                if not path.exists() or path.read_bytes()!=content:
                    atomic(path, content)
                    changed_providers.append(provider['name'])
        if same_configuration:
            try:
                for port in ports:check_relay(port,state["token"])
            except (OSError,Problem):pass
            else:
                for name in ([p['name'] for p in policy['providers']] if policy and force_providers else changed_providers):
                    clash('/providers/rules/'+quote(name,safe=''), 'PUT', timeout=10)
                    expected_path = next(urlsplit(p['url']).path for p in policy['providers'] if p['name']==name)
                    if expected_path not in server.received:
                        raise Problem('规则未从本次更新读取，已取消切换。')
                if policy:
                    loaded = clash('/providers/rules')['providers']
                    for provider in policy['providers']:
                        actual_provider = loaded.get(provider['name'], {})
                        if (actual_provider.get('vehicleType') != 'HTTP' or
                                actual_provider.get('ruleCount') != max(1, len(provider['payload']))):
                            raise Problem('应用规则热更新尚未确认，已取消切换。')
                return
        if live_only:
            raise Problem('现有转发入口尚未就绪，已保留其他应用连接。')
        clash("/configs?force=false","PUT",{"payload":compact(live)},timeout=25)
        actual=clash("/rules")["rules"]
        for r in start_rules:
            if not r.get("extra",{}).get("disabled"): continue
            same=[a for a in actual if (a["type"],a["payload"],a["proxy"])==(r["type"],r["payload"],r["proxy"])]
            if len(same)==1 and not same[0].get("extra",{}).get("disabled"):
                clash("/rules/disable","PATCH",{str(same[0]["index"]):True})
        refreshed=clash("/proxies")["proxies"]
        for name,prior in selected.items():
            group=refreshed.get(name,{})
            if group.get("now")!=prior and prior in group.get("all",[]):
                clash("/proxies/"+quote(name,safe=""),"PUT",{"name":prior})
        start=[(r["payload"],r["proxy"]) for r in actual if r["type"]=="ProcessPathRegex" and r["payload"].startswith("^(?<EgressMenu>")]
        expected=[(rule.split(",",2)[1],rule.split(",",2)[2]) for rule in expected_rules]
        if start!=expected:
            raise Problem("Clash 没有加载选中的出口。")
        expected_in=[(r.split(",")[1],r.split(",")[2]) for r in live["rules"] if r.startswith("IN-NAME,"+LISTENER_PREFIX)]
        actual_in=[(r["payload"],r["proxy"]) for r in actual if r["type"]=="InName" and r["payload"].startswith(LISTENER_PREFIX)]
        if expected_in!=actual_in: raise Problem("Clash 未加载应用流量入口的分流规则。")
        for port in ports:
            check_relay(port,state["token"])


def check_relay(port,token):
    def take(sock,n):
        buf=b""
        while len(buf)<n:
            part=sock.recv(n-len(buf))
            if not part: raise Problem("本机 SOCKS5 转发入口提前关闭。")
            buf+=part
        return buf
    with socket.create_connection(("127.0.0.1",port),timeout=2) as s:
        s.sendall(b"\x05\x01\x02")
        if take(s,2)!=b"\x05\x02": raise Problem("本机转发入口未启用身份验证。")
        user=b"net-egress"; secret=token.encode()
        s.sendall(b"\x01"+bytes([len(user)])+user+bytes([len(secret)])+secret)
        if take(s,2)!=b"\x01\x00": raise Problem("本机转发入口验证失败。")


def apply_route(path,choice,simple=False):
    with locked():
        state=read_json(STATE)
        follow_subscription=simple and choice=='proxy'
        if simple:
            choice=simple_mode.route_choice(choice,
                clash('/rules')['rules'] if choice=='proxy' else (),
                clash('/proxies')['proxies'] if choice=='proxy' else {})
        if state.get("protectServices") and protection.is_service(path) and choice!="DEFAULT":
            raise Problem("该进程承载本机业务服务，当前使用受保护的原网络路径。")
        if state.get("setupPending") or not state.get('policyProviderMode'):
            raise Problem('热更新尚待初始化，已保留现有应用设置和连接。')
        if clash('/configs')['mode']!='rule':
            raise Problem("Clash Verge 当前没有使用规则模式。请先切换到「规则」。")
        protection.check_services(state.get("serviceChecks",[]))
        if JOURNAL.exists(): recover()
        state=read_json(STATE)
        extension=mc("appRouting.status")
        if choice!="DEFAULT":
            if extension["state"] not in ("on","awaitingUserApproval","enabling","requiresReboot"):
                raise Problem("MClash 分流未连接。请先在菜单栏连接 MClash。")
            live=clash("/proxies")["proxies"]
            if choice!="DIRECT" and (choice not in live or choice in RESERVED or any(c in choice for c in ",\r\n")):
                raise Problem("这个出口已不在当前 Clash 订阅中。")
        new=copy.deepcopy(state)
        if follow_subscription:
            # Reuse an existing automatic relay across subscription changes.
            # It no longer represents a particular provider or remote node.
            choice=next((o['choice'] for o in state['overrides'].values()
                         if o['choice']!='DIRECT' and o.get('followSubscription',True)),choice)
            stable = state.get('proxyRelayChoice')
            if stable not in state['targets']:
                stable = next((key for key, follow in state.get('retainedRelayChoices', {}).items()
                               if follow and key in state['targets']), None)
            if stable in state['targets']:
                choice = stable
            new['proxyRelayChoice'] = choice
        if not state["overrides"]: new["hookMode"]=stat.S_IMODE(HOOK.stat().st_mode)
        if choice not in ("DIRECT","DEFAULT") and choice not in new["targets"]:
            target={"key":hashlib.sha256(choice.encode()).hexdigest()[:20],
                    "kind":"group" if isinstance(live[choice].get("all"),list) else "node",
                    "port":available_port([p["port"] for p in new["targets"].values()]+[new["corePort"]])}
            extra_profile={**new,"targets":{choice:target}}
            target["profileID"]=import_relays(extra_profile,"NetworkEgress-"+target["key"]+".yaml")
            new["targets"][choice]=target
            state["targets"][choice]=target
            save_json(STATE,state)
        if choice=="DEFAULT": new["overrides"].pop(path,None)
        else:
            new["overrides"][path]=inspect_target(path,choice)
            if choice!='DIRECT':new["overrides"][path]['followSubscription']=follow_subscription
        if new["overrides"]==state["overrides"]:return {"ok":True}
        retain_relay_choices(new, state)
        if mc("configuration.snapshot",{"nodeLimit":1})["currentWorkspaceID"]!=workspace.WORKSPACE:
            raise Problem("MClash 当前工作区已变更，请先切回「网络出口」。")
        workspace.live_session(mc)
        save_json(JOURNAL,{"before":state,"after":new})
        try:
            update_cv(new,live_only=True)
            if extension["state"]=="on":
                workspace.apply(mc,new,state.get("appliedOverrides",state["overrides"]))
                new["appliedOverrides"]=copy.deepcopy(new["overrides"])
            else:
                new["appliedOverrides"]=state.get("appliedOverrides",state["overrides"])
            if PAUSED.exists():
                raise Problem("分流已暂停，本次变更已取消。")
            protection.check_services(new.get("serviceChecks",[]))
            save_json(STATE,new);JOURNAL.unlink()
        except Exception as e:
            try: recover()
            except Exception as rollback:
                raise Problem("配置尚待恢复。%s / %s"%(e,rollback))
            raise
        return {"ok":True,"pendingApproval":extension["state"]!="on"}


def reset_all_auto():
    """Maintenance operation: remove manual choices in one protected transaction."""
    with locked():
        if JOURNAL.exists():recover()
        state=read_json(STATE)
        count=len(state['overrides'])
        if not count:return {'ok':True,'resetCount':0}
        if not state.get('policyProviderMode'):
            raise Problem('热更新尚待初始化，已保留现有应用设置和连接。')
        if clash('/configs')['mode']!='rule':
            raise Problem('Clash Verge 当前没有使用规则模式。请先切换到「规则」。')
        if mc('configuration.snapshot',{'nodeLimit':1})['currentWorkspaceID']!=workspace.WORKSPACE:
            raise Problem('MClash 当前工作区已变更，请先切回「网络出口」。')
        protection.check_services(state.get('serviceChecks',[]))
        extension=mc('appRouting.status')['state']
        new=copy.deepcopy(state)
        new['overrides']={}
        retain_relay_choices(new, state)
        save_json(JOURNAL,{'before':state,'after':new})
        try:
            # Move apps off their dedicated listeners before removing listeners.
            if extension=='on':
                workspace.apply(mc,new,state.get('appliedOverrides',state['overrides']))
                new['appliedOverrides']={}
            else:new['appliedOverrides']=state.get('appliedOverrides',state['overrides'])
            update_cv(new,live_only=True)
            if PAUSED.exists():raise Problem('分流已暂停，本次变更已取消。')
            protection.check_services(new.get('serviceChecks',[]))
            save_json(STATE,new)
            JOURNAL.unlink()
        except Exception as error:
            try:recover()
            except Exception as rollback:raise Problem('配置尚待恢复。%s / %s'%(error,rollback))
            raise
        return {'ok':True,'resetCount':count}


def prepare_live_policy():
    """Explicit maintenance only: initial provider registration reloads Clash once.

    Never call this from a menu button, startup, recovery, or periodic sync.
    Existing choices are identical before/after; MClash is not reactivated.
    """
    with locked():
        if JOURNAL.exists():
            recover()
        state = read_json(STATE)
        workspace.live_session(mc)
        protection.check_services(state.get('serviceChecks', []))
        new = copy.deepcopy(state)
        prepare_policy(new)
        new['routingVersion'] = ROUTING_VERSION
        new['configGeneration'] = CONFIG.stat().st_mtime_ns
        save_json(JOURNAL, {'kind':'live-policy-setup','before':state,'after':new})
        try:
            update_cv(new)
            update_cv(new,live_only=True,force_providers=True)
            protection.check_services(new.get('serviceChecks', []))
            save_json(STATE,new)
            JOURNAL.unlink()
        except Exception:
            # Recovery inspects the loaded side and never repeats this reload.
            recover()
            raise
        return {'ok':True,'liveUpdatesReady':True}


def recover():
    j=read_json(JOURNAL); saved=read_json(STATE)
    if j.get('kind') == 'live-policy-setup':
        # Both sides have the same user choices. Recover the side already
        # loaded by Clash, without ever repeating the initialization reload.
        loaded = any(r.get('type')=='RuleSet' and r.get('payload','').startswith(LISTENER_PREFIX+'policy-')
                     for r in clash('/rules')['rules'])
        desired = j['after'] if loaded else j['before']
        update_cv(desired,live_only=True,force_providers=True)
        save_json(STATE,desired)
        JOURNAL.unlink()
        return
    if PAUSED.exists():
        mc("core.disconnect",interaction=True)
        blank=copy.deepcopy(saved);blank["overrides"]={};blank.pop("systemPolicy",None)
        update_cv(blank)
        JOURNAL.unlink()
        return
    other=j["after"] if saved==j["before"] else j["before"]
    retain_relay_choices(saved, other)
    update_cv(saved,live_only=True,force_providers=True)
    if mc("appRouting.status")["state"]=="on":
        workspace.apply(mc,saved,set(other["overrides"])|set(other.get("appliedOverrides",{})),force_live=True)
        saved["appliedOverrides"]=copy.deepcopy(saved["overrides"])
        save_json(STATE,saved)
    JOURNAL.unlink()


def reconcile():
    with locked():
        if JOURNAL.exists(): recover()
        state=read_json(STATE)
        applied=state.get("appliedOverrides",state["overrides"])
        if applied==state["overrides"]:return {"ok":True}
        if mc("appRouting.status")["state"]!="on":return {"pendingApproval":True}
        workspace.apply(mc,state,applied)
        state["appliedOverrides"]=copy.deepcopy(state["overrides"])
        save_json(STATE,state)
        return {"ok":True}


def sync_system(force=False):
    with locked():
        if JOURNAL.exists(): recover()
        try:
            state=read_json(STATE)
        except FileNotFoundError:
            return {'ok':True,'setupPending':True}
        if state.get('setupPending') or not state.get('policyProviderMode'):
            # Initialization must be scheduled explicitly. Periodic sync must
            # not repeatedly attempt or roll back an unfinished migration.
            return {'ok':True,'upgradePending':True}
        generation=CONFIG.stat().st_mtime_ns
        mixed=clash("/configs")["mixed-port"]
        current=system_route.endpoint(mixed)
        if (not force and state.get("policyVersion")==4 and state.get("routingVersion")==ROUTING_VERSION and
                state.get("configGeneration")==generation and
                all((state.get("systemPolicy") or {}).get(k)==v for k,v in current.items())):
            if not mc("core.status")["connected"]:
                mc("core.connect",timeout=45)
                protection.check_services(state.get("serviceChecks",[]))
            return {"ok":True}
        if mc("configuration.snapshot",{"nodeLimit":1})["currentWorkspaceID"]!=workspace.WORKSPACE:
            raise Problem("MClash 当前工作区已变更，请先切回「网络出口」。")
        # A pre-existing upstream outage must not prevent its own repair.
        # Roll back only if this change makes a previously reachable service fail.
        healthy_before=protection.reachable_services(state.get("serviceChecks",[]))
        new=copy.deepcopy(state)
        for policy in new["overrides"].values():
            if "signingIDs" not in policy:
                policy["signingIDs"]=signing_ids(policy["executables"],policy.get("bundleID"))
        system_route.prepare(new,yaml_config(),mixed,inspect_target,available_port,import_profile)
        new["configGeneration"]=generation
        new["routingVersion"]=ROUTING_VERSION
        for policy in new["overrides"].values():
            if policy['choice']!='DIRECT':policy.setdefault('followSubscription',True)
        if "hookMode" not in new: new["hookMode"]=stat.S_IMODE(HOOK.stat().st_mode)
        save_json(JOURNAL,{"before":state,"after":new})
        try:
            update_cv(new,live_only=True)
            if not workspace.same_transport(state,new):
                workspace.apply(mc,new,state.get("appliedOverrides",state["overrides"]))
            if not mc("core.status")["connected"]:
                mc("core.connect",timeout=45)
            if PAUSED.exists(): raise Problem("分流已暂停，本次变更已取消。")
            protection.check_services(healthy_before)
            new["appliedOverrides"]=copy.deepcopy(new["overrides"])
            save_json(STATE,new)
            JOURNAL.unlink()
        except Exception as error:
            try: recover()
            except Exception as rollback: raise Problem("配置尚待恢复。%s / %s"%(error,rollback))
            raise
        return {"ok":True,"changed":True}


def pause():
    PAUSED.touch(mode=0o600)
    mc("core.disconnect",interaction=True)
    with locked():
        state=read_json(STATE)
        blank=copy.deepcopy(state);blank["overrides"]={};blank.pop("systemPolicy",None)
        update_cv(blank)
        snap=mc("configuration.snapshot",{"nodeLimit":1})
        for entry in snap["document"]["entrances"]:
            if entry["id"]==workspace.ENTRANCE:entry["enabled"]=False
        mc("configuration.apply",{"document":snap["document"],"expectedRevision":snap["configurationRevision"]},interaction=True)
        if JOURNAL.exists():JOURNAL.unlink()
    return {"ok":True,"paused":True}


def resume():
    PAUSED.unlink(missing_ok=True)
    try:return sync_system(force=True)
    except Exception:
        pause()
        raise


def menu_snapshot():
    """Read saved choices and readiness without collecting per-connection activity."""
    try:
        state=read_json(STATE)
    except FileNotFoundError:
        state={"setupPending":True}
    if state.get("setupPending"):
        return {"apps":[], "status":"first-run", "ready":False, "paused":False,
                "pending":False, "warning":None, "protectedPaths":sorted(protection.APP_PATHS)}
    paused=PAUSED.exists()
    clash_mode=''
    extension='unavailable'
    try:clash_mode=clash('/configs')['mode']
    except Exception:pass
    if not paused:
        try:extension=mc('appRouting.status')['state']
        except Exception:pass
    needs_recovery=False
    if JOURNAL.exists():
        with (APP_DATA/'update.lock').open('a') as handle:
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:pass
            else:needs_recovery=JOURNAL.exists()
    if paused:status='paused'
    elif not clash_mode:status='clash-off'
    elif clash_mode!='rule':status='clash-mode'
    elif needs_recovery:status='recovery'
    else:status={'on':'ready','awaitingUserApproval':'approval','requiresReboot':'reboot',
                 'enabling':'connecting'}.get(extension,'offline')
    if status=='ready' and not state.get('policyProviderMode'):
        status='upgrade-pending'
    rows=[]
    for path,value in state['overrides'].items():
        protected=bool(state.get('protectServices') and protection.is_service(path))
        if simple_mode.visible_app(path,protected):
            rows.append({'path':path,'mode':simple_mode.display_mode(value['choice'])})
    warning=None
    if status=='ready' and any(o['choice']!='DIRECT' and o.get('followSubscription',True)
                               for o in state['overrides'].values()):
        try:simple_mode.proxy_target(clash('/rules')['rules'],clash('/proxies')['proxies'])
        except (KeyError,ValueError,Problem,OSError):
            warning='当前订阅未选好代理线路。请在 Clash 中选好线路，或将应用恢复自动。'
    return {'apps':rows,'status':status,'ready':status=='ready','paused':paused,
            'warning':warning,
            'pending':not paused and state.get('appliedOverrides',state['overrides'])!=state['overrides'],
            'protectedPaths':sorted(protection.APP_PATHS) if state.get('protectServices') else []}


def main(args):
    if args==['repair-menu']:return menu_visibility.repair()
    if args==['menu-snapshot']:return menu_snapshot()
    if args==["pause"]:return pause()
    if args==["resume"]:return resume()
    if PAUSED.exists():
        if args==["snapshot"]:
            state=read_json(STATE)
            state["overrides"]={}
            result=observation.snapshot(state,clash,lambda *args: {"state":"paused"})
            result["pending"]=False
            return result
        if args in (["sync-system"],["reconcile"]):
            return {"ok":True,"paused":True}
        raise Problem("全机分流已停用，网络出口当前仅供查看。")
    if args==["setup"]:return setup()
    if args==['prepare-live-policy']:return prepare_live_policy()
    if args==['reset-all-auto']:return reset_all_auto()
    if args==["sync-system"]:return sync_system()
    if args==["snapshot"]:
        state=read_json(STATE)
        result=observation.snapshot(state,clash,mc)
        if JOURNAL.exists():
            with (APP_DATA/"update.lock").open("a") as handle:
                try:
                    fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    if JOURNAL.exists():result["notes"].append("上次操作中断，需恢复规则")
        result["pending"]=state.get("appliedOverrides",state["overrides"])!=state["overrides"]
        return result
    if args==["reconcile"]:return reconcile()
    if args==["recover"]:
        with locked():
            if JOURNAL.exists():recover()
        return {"ok":True}
    if len(args)==3 and args[0]=="set":return apply_route(args[1],args[2])
    if len(args)==3 and args[0]=='set-simple':return apply_route(args[1],args[2],simple=True)
    if args==["retry"]:
        return mc("appRouting.retry",timeout=55)
    raise Problem("未知操作。")


if __name__=="__main__":
    try: print(compact(main(sys.argv[1:])))
    except Exception as e: print(compact({"error":str(e)})); sys.exit(1)
