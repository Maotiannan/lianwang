"""MClash public Configuration API. Only objects in our dedicated workspace are managed."""
import uuid
import copy
import system_route
import protection
import engine


def identifier(kind, value=""):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "local.network-egress/" + kind + "/" + value))


WORKSPACE = identifier("workspace")
ENTRANCE = identifier("app-routing")
LIVE_UPDATE = identifier('live-update-marker')
DNS = identifier("dns")
SYSTEM_GROUP = identifier("macos-system-proxy-group")
ROUTER_PATHS = (
    "/Library/Application Support/clash-verge-service/cores/verge-mihomo",
    "/Library/Application Support/clash-verge-service/cores/verge-mihomo-alpha",
    "/Applications/Clash Verge.app/Contents/MacOS/verge-mihomo",
    "/Applications/Clash Verge.app/Contents/MacOS/verge-mihomo-alpha",
    "/Applications/MClash.app/Contents/Resources/Core/mclash-xray",
    engine.XRAY,
)


def upsert(records, new):
    for index, old in enumerate(records):
        if old["id"] == new["id"]:
            records[index] = new
            return
    records.append(new)


def config_for(snapshot, state, old):
    doc = snapshot["document"]
    source_ids={state["profileID"]}|{p["profileID"] for p in state["targets"].values() if p.get("profileID")}
    system = state.get("systemPolicy") or {}
    if system: source_ids.add(system["profileID"])
    known = {v["displayName"]: v["id"] for v in snapshot["nodes"]["items"]
             if source_ids.intersection(v.get("sourceLinks", []))}
    direct = {"kind":"direct"}
    upsert(doc["dnsPolicies"], {"id":DNS, "name":"网络出口 DNS", "mode":"redirHost",
                                "takeoverEnabled":False})
    def relay_group(group_id, group_name, node_name):
        node = known.get(node_name)
        if not node:
            raise ValueError("MClash 本机转发配置里找不到所选出口。")
        upsert(doc["proxyGroups"], {"id":group_id, "name":group_name, "type":"select",
                                    "enabled":True, "membersUpdate":[{"kind":"node","id":node}]})
        return {"kind":"proxyGroup","proxyGroupID":group_id}
    if system:
        default_group = SYSTEM_GROUP
        default_action = relay_group(default_group, "macOS 系统代理", system_route.CORE_NAME)
    else:
        default_group = next(g["id"] for g in doc["proxyGroups"] if g["id"] not in {
            identifier("group", n) for n in state["targets"]} | {SYSTEM_GROUP})
        default_action = direct
    upsert(doc["entrances"], {"id":ENTRANCE, "name":"网络出口 · 应用分流", "kind":"appRouting",
                              "enabled":True, "bindAddress":"127.0.0.1", "defaultAction":default_action})
    group_ids = [default_group]
    rule_ids = []
    def rule(rule_id, matchers, action):
        rule_ids.append(rule_id)
        upsert(doc["rules"], {"id":rule_id, "enabled":True, "priority":len(rule_ids),
                              "matchersUpdate":matchers, "action":action, "unavailableFallback":"reject",
                              "workspaceScope":WORKSPACE})
    rule(identifier("proxy-engines"), [{"kind":"processPath", "value":p} for p in ROUTER_PATHS] +
         [{"kind":"application","value":p} for p in ("verge-mihomo","verge-mihomo-alpha","mclash-xray")], direct)
    if state.get("protectServices"):
        rule(identifier("protected-services"),
             [{"kind":"application","value":p} for p in protection.IDENTITIES] +
             [{"kind":"processPath","value":p} for p in sorted(protection.EXECUTABLES)], direct)
    rule(identifier("native-dns"),protection.all_sources()+[{"kind":"port","value":"53"}],direct)
    if system.get("bypassMatchers"):
        rule(identifier("system-exceptions"),protection.all_sources()+system["bypassMatchers"],direct)
    retired = {identifier("rule", path) for path in set(old) - set(state["overrides"])}
    for item in doc["rules"]:
        if item["id"] in retired:
            item["enabled"] = False
    for path, override in sorted(state["overrides"].items()):
        if state.get("protectServices") and protection.is_service(path): continue
        choice = override["choice"]
        action = direct
        if choice != "DIRECT" or system:
            endpoint = state["targets"][choice]
            group_id = identifier("group", choice)
            action = relay_group(group_id, "EG-"+endpoint["key"], "relay-"+endpoint["key"])
            if group_id not in group_ids: group_ids.append(group_id)
        rule_id = identifier("rule", path)
        matchers = [{"kind":"processPath", "value":p} for p in override["executables"]]
        for signing_id in sorted(set(override.get("signingIDs", [])) | ({override["bundleID"]} if override.get("bundleID") else set())):
            matchers.append({"kind":"application","value":signing_id})
        if not matchers: raise ValueError("无法识别目标可执行文件。")
        rule(rule_id, matchers, action)
    if state.get("testOnly"):
        rule(identifier("test-default"),protection.all_sources(),direct)
        rule(identifier("test-fallback"), [], direct)
    for block in system.get("blocks", []):
        tag = block["key"]
        group_id = identifier("inherited-group", tag)
        action = relay_group(group_id, "原进程规则-"+tag, "relay-"+tag)
        group_ids.append(group_id)
        rule(identifier("inherited-rule", tag), block["matchers"], action)
    upsert(doc["workspaces"], {"id":WORKSPACE,"name":"网络出口","nodeScope":"allEnabled",
                               "routingMode":"rule","globalProxyGroupID":default_group,
                               "dnsPolicyID":DNS,"proxyGroupIDsUpdate":group_ids,"ruleIDsUpdate":rule_ids,
                               "ruleSetIDsUpdate":[],"entranceIDsUpdate":[ENTRANCE]})
    return {"document":doc, "expectedRevision":snapshot["configurationRevision"]}


def same_transport(before,after):
    """Subscription targets live in Clash; only transport changes need Xray."""
    def relevant(state):
        result={key:state.get(key) for key in ('policyVersion','protectServices','systemPolicy',
                                              'targets','identityRelays','token','testOnly')}
        result['overrides']={path:{k:v for k,v in item.items() if k!='followSubscription'}
                             for path,item in state['overrides'].items()}
        return result
    applied=dict(before,overrides=before.get('appliedOverrides',before['overrides']))
    return relevant(applied)==relevant(after)


def live_session(call):
    """Refuse a route edit unless the existing data plane is fully ready."""
    core = call('core.status')
    capture = call('appRouting.status')
    if (not core.get('connected') or core.get('controller') != 'ready' or
            core.get('state') != 'running' or not core.get('startedAt') or
            capture.get('state') != 'on' or not capture.get('enabled') or
            capture.get('dnsEnabled')):
        raise ValueError('分流后台尚未就绪，已保留现有连接；请稍后重试。')
    return core['startedAt'], capture['revision']


def apply_live(call, snapshot, request):
    """Use the vendor's in-place capture update, never workspace activation.

    In MClash 1.6.2 the generic rules API rejects unified configurations. Its
    supported enable command compiles a saved configuration and updates an
    already-enabled, ready provider in place. A disabled, private marker is
    temporarily the first workspace entrance so that command applies the new
    rules. The real entrance stays enabled even if this process crashes.
    Put the real entrance first in the catalog because the vendor selects
    that entrance when applying the command. Other entrances are preserved.
    """
    session = live_session(call)
    staged = copy.deepcopy(request)
    entrances = staged['document']['entrances']
    target = next(e for e in entrances if e['id'] == ENTRANCE)
    entrances.remove(target)
    entrances.insert(0, target)
    upsert(entrances, {'id': LIVE_UPDATE, 'name': '联网 · 规则更新标记', 'kind': 'appRouting',
                      'enabled': False, 'bindAddress': '127.0.0.1', 'defaultAction': {'kind': 'direct'}})
    active = next(w for w in staged['document']['workspaces'] if w['id'] == WORKSPACE)
    active['entranceIDsUpdate'] = [LIVE_UPDATE, ENTRANCE]
    def finish_staging():
        current = call('configuration.snapshot', {'nodeLimit': 1})
        active = next(w for w in current['document']['workspaces'] if w['id'] == WORKSPACE)
        active['entranceIDsUpdate'] = [ENTRANCE]
        return call('configuration.apply', {'document': current['document'],
                    'expectedRevision': current['configurationRevision']}, interaction=True)['configurationRevision']
    saved = False
    try:
        call('configuration.apply', staged, interaction=True)
        saved = True
        if live_session(call) != session:
            raise ValueError('后台状态刚发生变化，已取消更新并保留现有连接。')
        call('appRouting.setEnabled', {'enabled': True}, timeout=45)
        after = live_session(call)
        final = call('configuration.snapshot', {'nodeLimit': 1})
        enabled = next(e['enabled'] for e in final['document']['entrances'] if e['id'] == ENTRANCE)
        if after[0] != session[0] or after[1] <= session[1] or not enabled:
            raise ValueError('未能确认规则已在现有连接上更新，已取消本次切换。')
        return finish_staging()
    except Exception:
        if saved:
            # Clear only our marker. The outer journal restores the previous
            # desired rules with another live transaction, never activation.
            # A snapshot is sparse and cannot be used as a whole rollback:
            # doing so would lose membership lists or reject newly added IDs.
            finish_staging()
        raise


def apply(call, state, old=(), *, allow_activation=False, force_live=False):
    snap = call("configuration.snapshot", {"nodeLimit":200})
    if snap["nodes"]["hasMore"]:
        offset = len(snap["nodes"]["items"])
        while offset < snap["nodes"]["total"]:
            page = call("configuration.snapshot", {"nodeLimit":200,"nodeOffset":offset})
            if page["configurationRevision"]!=snap["configurationRevision"]:
                raise ValueError("MClash 的配置刚发生变化，请重试。")
            items=page["nodes"]["items"]
            if not items: raise ValueError("读取 MClash 节点未完成。")
            snap["nodes"]["items"].extend(items)
            offset+=len(items)
    request = config_for(copy.deepcopy(snap), state, old)
    plan = call("configuration.plan", request)
    if not plan["valid"]:
        errors=[d.get("message") or d.get("code","") for d in plan.get("diagnostics",[]) if d.get("severity")=="error"]
        raise ValueError("MClash 配置校验失败。"+" ".join(errors[:4]))
    revision = snap["configurationRevision"]
    needs_activation = not snap['unifiedConfigurationEnabled'] or snap['currentWorkspaceID'] != WORKSPACE
    if allow_activation:
        if plan['changed']:
            revision = call('configuration.apply', request, interaction=True)['configurationRevision']
        if plan['changed'] or needs_activation:
            revision = call('configuration.workspace.activate', {'id': WORKSPACE, 'expectedRevision': revision},
                            interaction=True, timeout=55)['configurationRevision']
    else:
        if needs_activation:
            raise ValueError('当前分流工作区尚未就绪；为保留现有连接，已取消切换。')
        if plan['changed'] or force_live:
            revision = apply_live(call, snap, request)
    removed = {identifier("rule", path) for path in set(old) - set(state["overrides"])}
    for item in request["document"]["rules"]:
        if item["id"] in removed:
            receipt=call("configuration.delete", {"kind":"rule", "id":item["id"], "expectedRevision":revision}, interaction=True)
            revision=receipt["configurationRevision"]
