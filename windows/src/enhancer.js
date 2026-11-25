// Installed after the user's global Clash Verge script. Only the names below are reserved.
function lianwangConfig(config, token) {
  const group = 'LW-Windows-Proxy';
  const names = ['LW-Windows-direct', 'LW-Windows-proxy'];
  const inbound = 'LW-Windows-in';
  const providers = config['rule-providers'] || {};
  const groups = config['proxy-groups'] || [];
  const listeners = config.listeners || [];
  if (names.some(n => n in providers) || groups.some(g => g.name === group) ||
      listeners.some(l => l.name === inbound || Number(l.port) === 29842) ||
      (config.proxies || []).some(p => p.name === group)) {
    throw new Error('联网预留的名称或 29842 端口已被使用');
  }
  const excluded = ['direct', 'reject', 'reject-drop', 'pass', 'compatible', 'dns'];
  const usable = (config.proxies || []).filter(p => !excluded.includes(String(p.type).toLowerCase()));
  const use = Object.keys(config['proxy-providers'] || {});
  const target = usable.length || use.length ? group : 'REJECT';
  if (target === group) {
    config['proxy-groups'] = groups.concat([{ name: group, type: 'select', hidden: true,
      proxies: usable.map(p => p.name), use: use, 'exclude-type': excluded.join('|'),
      'empty-fallback': 'REJECT' }]);
  }
  config.listeners = listeners.concat([{name: inbound, type:'socks', listen:'127.0.0.1',
    port:29842, udp:true, proxy:target, users:[{username:'lw', password:token}]}]);
  config['rule-providers'] = Object.assign({}, providers);
  names.forEach((n, i) => {
    config['rule-providers'][n] = { type:'http', behavior:'classical', format:'text',
      url:'http://127.0.0.1:29841/' + token + '/' + (i ? 'proxy' : 'direct'),
      interval:0, proxy:'DIRECT', payload:['PROCESS-PATH-REGEX,^$a'] };
  });
  config['find-process-mode'] = 'always';
  config.rules = ['RULE-SET,' + names[0] + ',DIRECT', 'RULE-SET,' + names[1] + ',' + target,
    'RULE-SET,' + names[1] + ',REJECT'].concat(config.rules || []);
  return config;
}
