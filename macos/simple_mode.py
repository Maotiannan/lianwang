"""Translate the two user-facing choices without changing Clash's defaults."""

NON_PROXY_TYPES = {'Direct', 'Reject', 'RejectDrop', 'Pass', 'PassRule', 'Compatible', 'Dns'}


def proxy_target(rules, proxies):
    target = next((rule['proxy'] for rule in reversed(rules)
                   if rule.get('type') == 'Match'), None)

    def uses_proxy(name, seen):
        if name in seen:
            return False
        item = proxies.get(name)
        if not item or item.get('type') in NON_PROXY_TYPES:
            return False
        visited = seen | {name}
        if item.get('now'):
            return uses_proxy(item['now'], visited)
        if isinstance(item.get('all'), list):
            return bool(item['all']) and all(uses_proxy(child, visited) for child in item['all'])
        return True

    if not target or not uses_proxy(target, set()):
        raise ValueError('Clash 当前没有选好代理线路。请在 Clash 中选好线路后重试。')
    return target


def route_choice(mode, rules=(), proxies=None):
    if mode == 'direct':
        return 'DIRECT'
    if mode == 'auto':
        return 'DEFAULT'
    if mode == 'proxy':
        return proxy_target(rules, proxies or {})
    raise ValueError('请选择直连或代理。')


def visible_app(path, protected=False):
    return (path.startswith('/') and path.lower().endswith('.app') and not protected
            and path.rsplit('/', 1)[-1] not in {'MClash.app', 'Clash Verge.app', '网络出口.app', '联网.app'})


def display_mode(choice):
    return 'auto' if choice is None else 'direct' if choice == 'DIRECT' else 'proxy'
