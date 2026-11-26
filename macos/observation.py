"""Observe local sockets and the two routing engines; keep hostnames and user data out of the UI."""
import csv
import ctypes
import os
import plistlib
import re
import subprocess
import workspace
import protection
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone


def app_root(path):
    if not path or not path.startswith("/"): return None
    match = re.search(r"\.app(?=/|$)",path,re.I)
    return path[:match.end()] if match else path


def info(path):
    try:
        with (Path(path)/"Contents/Info.plist").open("rb") as f: return plistlib.load(f)
    except (OSError,ValueError): return {}


def title(path):
    return Path(path).stem if path.lower().endswith(".app") else Path(path).name


def pid_path(pid):
    if pid<=0: return None
    buf=ctypes.create_string_buffer(4096)
    return os.fsdecode(buf.value) if LIB.proc_pidpath(pid,buf,len(buf))>0 else None


LIB=ctypes.CDLL("/usr/lib/libproc.dylib")
LIB.proc_pidpath.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32]
LIB.proc_pidpath.restype=ctypes.c_int


def sockets():
    data=subprocess.run(["/usr/bin/nettop","-n","-x","-L","1","-J","interface,state"],
                        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=5)
    if data.returncode: raise OSError("macOS 网络统计暂不可用")
    rows=defaultdict(lambda:{"interfaces":set(),"processes":set(),"local":False,"name":""})
    current=None
    for row in csv.reader(data.stdout.decode(errors="replace").splitlines()):
        if len(row)<3: continue
        label=row[0].strip()
        process=re.fullmatch(r"(.+)\.(\d+)",label)
        if process:
            name,pid=process.group(1),int(process.group(2))
            path=pid_path(pid)
            current=(app_root(path) if path else "pid:%d"%pid, path, name)
            continue
        if not current or not label.startswith(("tcp","udp")) or row[2].lower() in ("listen","closed"): continue
        interface=row[1].strip()
        if not interface: continue
        key,path,name=current
        item=rows[key]
        item["name"]=title(key) if key.startswith("/") else name
        if path: item["processes"].add(path)
        if interface=="lo0": item["local"]=True
        else: item["interfaces"].add(interface)
    return rows


def timestamp(value):
    if isinstance(value,(int,float)):
        return value+978307200 if value<1_200_000_000 else value
    if isinstance(value,str):
        try: return datetime.fromisoformat(value.replace("Z","+00:00")).timestamp()
        except ValueError: pass
    return 0


def snapshot(state,clash,mc):
    notes=[]
    try: table=sockets()
    except Exception:
        notes.append("macOS 网络统计暂不可用")
        table={}
    rows={}
    def item(path):
        if path not in rows:
            rows[path]={"path":path,"name":title(path),"seen":set(),"routes":set(),"recent":set(),
                        "local":False,"configured":state["overrides"].get(path,{}).get("choice")}
        return rows[path]
    for path,record in table.items():
        row=item(path);row["name"]=record["name"];row["seen"]=record["interfaces"];row["local"]=record["local"]
    cv_available=False
    live_choices={}
    bridge_flows=[]
    system=state.get("systemPolicy") or {}
    inherited={workspace.identifier("inherited-rule", b["key"]):b["key"] for b in system.get("blocks",[])}
    identities=defaultdict(set)
    for path in set(table) | set(state["overrides"]):
        override=state["overrides"].get(path,{})
        for value in (*override.get("signingIDs",[]), override.get("bundleID") or info(path).get("CFBundleIdentifier")):
            if value: identities[value].add(path)
    try:
        cv=clash("/configs");cv_available=True
        proxies=clash("/proxies")["proxies"]
        live_choices=proxies
        for c in clash("/connections")["connections"]:
            m=c.get("metadata") or {}
            path=app_root(m.get("processPath"))
            chain=c.get("chains") or []
            incoming=m.get("inboundName") or ""
            key=incoming[len("net-egress-menu-"):] if incoming.startswith("net-egress-menu-") else None
            if system and not key and m.get("processPath")==workspace.ROUTER_PATHS[-1]:
                key="system"
            if key and chain:
                bridge_flows.append((key,str(m.get("destinationPort","")),
                                     {str(s).lower() for s in (m.get("host"),m.get("destinationIP")) if s},chain[0]))
            if path and chain and not key:
                item(path)["routes"].add("Clash 直连" if chain[0]=="DIRECT" else chain[0])
    except Exception:
        cv={};notes.append("Clash Verge 未连接")
    ext="unavailable"
    try:
        capture=mc("appRouting.status");ext=capture["state"]
        if ext=="on":
            events=mc("appRouting.activities.list",{"limit":150})["items"]
            now=datetime.now(timezone.utc).timestamp()
            for e in events:
                source=e.get("source") or {}
                path=app_root(source.get("executablePath") or pid_path(source.get("processIdentifier",0)))
                if not path:
                    identity=source.get("bundleIdentifier") or source.get("signingIdentifier")
                    if identity:
                        known=identities.get(identity,set())
                        path=next(iter(known)) if len(known)==1 else "身份/"+identity
                closed=timestamp(e.get("closedAt") or e.get("endedAt"))
                if not closed and e.get("relayState")=="notApplicable": closed=timestamp(e.get("startedAt"))
                if not path or (closed and now-closed>75): continue
                kind=e.get("effectiveAction") or {}
                row=item(path)
                observed=row["recent"] if closed else row["routes"]
                if isinstance(kind,dict) and ("direct" in kind or "failOpen" in kind):
                    dest=e.get("destination") or {}
                    addr=str(dest.get("ipAddress") or "")
                    if not(addr.startswith("127.") or addr=="::1" or addr.startswith("::ffff:127.")):
                        observed.add("直连")
                elif isinstance(kind,dict) and ("reject" in kind or "rejected" in kind): observed.add("已拦截")
                elif isinstance(kind,dict) and "mihomo" in kind:
                    name=(state["overrides"].get(path) or {}).get("choice")
                    rule_id=str(e.get("matchedRuleIdentifier","")).lower()
                    key=inherited.get(rule_id)
                    if rule_id==workspace.identifier("rule",path):
                        key=(state["targets"].get(name) or {}).get("key")
                    elif rule_id=="mclash-compiled-workspace-catch-all" and system:
                        key="system"
                    dest=e.get("destination") or {}
                    targets={str(v).lower() for v in (dest.get("ipAddress"),dest.get("hostname")) if v}
                    matches={node for k,port,hosts,node in bridge_flows if k==key and
                             port==str(dest.get("port","")) and hosts&targets}
                    relay=e.get("relayState")
                    if relay=="failed": observed.add("转发失败")
                    elif key and len(matches)==1:
                        observed.update("Clash 直连" if m=="DIRECT" else m for m in matches)
                    elif relay in ("ready","relaying","completed"):
                        observed.add("已转发至系统代理" if key=="system" else "系统扩展已转发")
                    else: observed.add("正在连接")
    except Exception: pass
    for path in state["overrides"]: item(path)
    apps=[]
    for path,row in rows.items():
        protected=bool(state.get("protectServices") and protection.is_service(path))
        routes=sorted(row["routes"]);devices=sorted(row["seen"])
        if routes: observed="、".join(routes)
        elif row["recent"]: observed="最近　"+"、".join(sorted(row["recent"]))
        elif devices: observed="网络接口 "+"、".join(devices)+" · 出口未确认"
        elif row["local"]: observed="仅观察到本机连接"
        else: observed="暂无活动连接"
        configured=None if protected else row["configured"]
        if configured and configured!="DIRECT" and live_choices.get(configured,{}).get("udp") is False:
            observed += " · 出口仅支持 TCP"
        if protected:observed="服务保护 · 原网络路径"
        apps.append({"path":path,"name":row["name"],"observed":observed,"configured":configured,"protected":protected,
                     "active":bool(routes or devices or row["local"] or row["recent"]),
                     "editable":path.startswith("/") and os.path.exists(path) and not protected})
    apps.sort(key=lambda a:(not a["configured"],not a["active"],a["name"].casefold()))
    targets=[{"name":n,"kind":"group" if isinstance(p.get("all"),list) else "node","supportsUDP":p.get("udp")}
             for n,p in live_choices.items() if p.get("type") not in {"Direct","Reject","RejectDrop","Pass","PassRule","Compatible","Dns"}
             and n not in {"DIRECT","REJECT","REJECT-DROP","GLOBAL","PASS","PASS-RULE","COMPATIBLE","DNS","MATCH"}
             and not any(c in n for c in ",\r\n")]
    if system.get("unsupported"):
        notes.append("订阅中有当前扩展无法识别来源进程的规则。")
    return {"apps":apps,"choices":sorted(targets,key=lambda a:a["name"].casefold()),"extension":ext,
            "systemEndpoint":("%s:%d"%(system["host"],system["port"])) if system else None,
            "clash":cv_available,"mode":cv.get("mode",""),"tun":bool((cv.get("tun") or {}).get("enable")),
            "notes":notes}
