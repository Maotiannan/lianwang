"""Keep the wrapper's entry visible when macOS regroups its bundled helper.

This only changes display preferences and refreshes the two display agents. It never
starts or stops the routing engine, Clash, or any network service.
"""
from pathlib import Path
import copy
import fcntl
import json
import os
import plistlib
import signal
import subprocess
import time

MAIN = 'local.network-egress.menu'
HELPER = 'one.leaper.mclash'
DOMAIN = Path.home() / 'Library/Group Containers/group.com.apple.controlcenter/Library/Preferences/group.com.apple.controlcenter'
DISPLAY_PROCESSES = {
    '/System/Library/CoreServices/ControlCenter.app/Contents/MacOS/ControlCenter',
    '/System/Library/CoreServices/MenuBarAgent.app/Contents/MacOS/MenuBarAgent',
}


def helper_menu_hidden(entries):
    if not isinstance(entries, list) or len(entries) % 2 or not all(isinstance(x, dict) for x in entries):
        raise ValueError('菜单栏显示记录格式已变化。')
    result = copy.deepcopy(entries)
    main = {'bundle': {'_0': MAIN}}
    helper = {'bundle': {'_0': HELPER}}
    for location, allowed in [(main, True), (helper, False)]:
        for offset in range(0, len(result), 2):
            if result[offset] == location:
                value = result[offset + 1]
                break
        else:
            value = {'location': location}
            result.extend([location, value])
        if location == main:
            items = [item for item in value.get('menuItemLocations', []) if item != helper]
            if main not in items:
                items.append(main)
            value['menuItemLocations'] = items
        else:
            value['menuItemLocations'] = [helper]
        value['isAllowed'] = allowed
    return result


def run(*args, data=None):
    return subprocess.run(list(map(str, args)), input=data, check=True,
                          capture_output=True, timeout=8).stdout


def repair(lock_path=None):
    lock_path = lock_path or Path.home() / 'Library/Application Support/NetworkEgress/menu-display.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        status_path = lock_path.with_suffix('.json')
        status = {'checkedAt': time.time(), 'parentPID': os.getppid(), 'phase': 'checking'}
        status_path.write_text(json.dumps(status))
        try:
            document = plistlib.loads(run('/usr/bin/defaults', 'export', DOMAIN, '-'))
            if 'trackedApplications' not in document:
                raise PermissionError('当前应用无法读取控制中心的菜单栏配置。')
            before = plistlib.loads(document['trackedApplications'])
            after = helper_menu_hidden(before)
        except Exception as error:
            detail = getattr(error, 'stderr', b'').decode(errors='replace').strip()
            status.update(phase='error', error=detail or str(error))
            status_path.write_text(json.dumps(status))
            raise
        if after == before:
            status.update(phase='ready', changed=False)
            status_path.write_text(json.dumps(status))
            return {'changed': False}
        document['trackedApplications'] = plistlib.dumps(after, fmt=plistlib.FMT_BINARY, sort_keys=False)
        run('/usr/bin/defaults', 'import', DOMAIN, '-', data=plistlib.dumps(document))
        rows = run('/bin/ps', '-U', str(os.getuid()), '-o', 'pid=,comm=').decode().splitlines()
        for row in rows:
            parts = row.strip().split(None, 1)
            # ControlCenter can write its cached, merged group back over the
            # correction; MenuBarAgent separately caches what it draws.
            if len(parts) == 2 and parts[1] in DISPLAY_PROCESSES:
                try:
                    os.kill(int(parts[0]), signal.SIGTERM)
                except ProcessLookupError:
                    pass
        status.update(phase='ready', changed=True)
        status_path.write_text(json.dumps(status))
        return {'changed': True}
