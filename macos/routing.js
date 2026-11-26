// Used by the Clash Verge profile enhancer and by live updates.
function applyNetworkEgress(config, entries) {
  const marker = "PROCESS-PATH-REGEX,^(?<EgressMenu>";
  const prefix = "net-egress-menu-";
  const available = new Set(["DIRECT", "REJECT"]);
  for (const item of (config.proxies || []).concat(config["proxy-groups"] || [])) {
    available.add(item.name);
  }
  const base = (config.rules || []).filter(rule => typeof rule !== "string" ||
    !(rule.startsWith(marker) || rule.startsWith("IN-NAME," + prefix) || rule.startsWith("RULE-SET," + prefix)));
  // Resolve the current subscription when Clash loads it. The local relay
  // identity stays stable, so switching providers needs no MClash restart.
  const fallback = base.slice().reverse().find(rule => typeof rule === "string" && rule.startsWith("MATCH,"));
  const defaultTarget = fallback ? fallback.split(",")[1] : null;
  const proxyNames = new Set((config.proxies || []).filter(item =>
    !["direct", "reject", "pass"].includes(item.type)).concat(config["proxy-groups"] || []).map(item => item.name));
  const subscriptionTarget = proxyNames.has(defaultTarget) ? defaultTarget : "REJECT";
  const listeners = (config.listeners || []).filter(listener => !String(listener.name).startsWith(prefix));
  const added = new Set();
  const overrides = [];
  const policy = entries.find(entry => entry.role === "policy");
  if (policy) {
    const providers = Object.fromEntries(Object.entries(config["rule-providers"] || {})
      .filter(([name]) => !name.startsWith(prefix + "policy-")));
    for (const p of policy.providers) {
      // The service owns its cache directory. User paths are not safe paths.
      // The short-lived local server is needed only for explicit rule updates.
      providers[p.name] = {type:"http", behavior:"classical", format:"yaml", url:p.url,
        proxy:"DIRECT", interval:0,
        payload:p.payload.length ? p.payload : ["PROCESS-PATH-REGEX,^$a"]};
      const target = p.followSubscription ? subscriptionTarget : (available.has(p.choice) ? p.choice : "REJECT");
      overrides.push("RULE-SET," + p.name + "," + target);
      if (target !== "DIRECT" && target !== "REJECT") overrides.push("RULE-SET," + p.name + ",REJECT");
    }
    config["rule-providers"] = providers;
  }
  const inherited = new Map();
  function listen(entry) {
    const endpoint = entry.endpoint;
    const name = prefix + endpoint.key;
    if (!added.has(name)) {
      added.add(name);
      listeners.push({name:name, type:"socks", listen:"127.0.0.1", port:endpoint.port,
        udp:true, users:[{username:"net-egress", password:entry.password}]});
    }
    return name;
  }
  for (const entry of entries) {
    if (entry.role === "policy") continue;
    if (entry.role === "inherited") {
      for (const raw of entry.rules) {
        if (base.includes(raw)) inherited.set(raw, listen(entry));
      }
      continue;
    }
    const target = entry.followSubscription ? subscriptionTarget :
      (available.has(entry.choice) ? entry.choice : "REJECT");
    if (entry.role !== "relay" && !policy) {
      overrides.push(marker + entry.pattern + ")," + target);
      if (target !== "DIRECT" && target !== "REJECT") {
        overrides.push(marker + entry.pattern + "),REJECT");
      }
    }
    if (entry.endpoint && !added.has(prefix + entry.endpoint.key)) {
      const match = "IN-NAME," + listen(entry) + ",";
      overrides.push(match + target);
      if (target !== "DIRECT" && target !== "REJECT") overrides.push(match + "REJECT");
    }
  }
  const ordered = [];
  const mirrored = new Set();
  for (const raw of base) {
    const name = inherited.get(raw);
    if (name && !mirrored.has(name)) {
      // A transparent relay loses the originating PID. This identity was matched
      // by macOS. Evaluate it at the same place as the original process rule.
      ordered.push("IN-NAME," + name + "," + raw.split(",")[2]);
      mirrored.add(name);
    }
    ordered.push(raw);
  }
  // App choices do not change listener order or rule topology.
  const providerRules = overrides.filter(r => r.startsWith("RULE-SET,"));
  const listenerRules = overrides.filter(r => r.startsWith("IN-NAME,"))
    .sort((a,b) => a.split(",")[1].localeCompare(b.split(",")[1]));
  config.rules = policy ? providerRules.concat(listenerRules, ordered) : overrides.concat(ordered);
  if (policy) listeners.sort((a,b) => String(a.name).localeCompare(String(b.name)));
  if (listeners.length || config.listeners) config.listeners = listeners;
  return config;
}
