"""Preserve the host's service network while applications are routed."""
from concurrent.futures import ThreadPoolExecutor
import subprocess
import os

APP_PATHS = {'/Applications/Docker.app'}
EXECUTABLES = {
    '/usr/sbin/mDNSResponder', '/usr/sbin/mDNSResponderHelper',
    '/opt/homebrew/bin/cloudflared', '/usr/local/bin/cloudflared',
}
IDENTITIES = (
    'com.docker.*', 'com.cloudflare.cloudflared', 'cloudflared',
    'com.apple.mDNSResponder', 'com.apple.mDNSResponderHelper',
)

def all_sources():
    return ([{"kind":"userID","value":str(os.getuid())},{"kind":"userID","value":"0"}] +
            [{"kind":"application","value":"*"+c+"*"} for c in "abcdefghijklmnopqrstuvwxyz0123456789"])


def is_service(path):
    return path in EXECUTABLES or any(path == p or path.startswith(p+'/') for p in APP_PATHS)


def reachable_services(checks):
    def probe(check):
        args=['/usr/bin/curl','-q','--silent','--show-error','--fail','--connect-timeout','2',
              '--max-time','5','--noproxy','*','--output','/dev/null','--write-out','%{http_code}',check['url']]
        if check.get('proxy'):
            args[args.index('--noproxy')+1]=''
            args += ['--proxy',check['proxy']]
        try:
            result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=7)
            return result.returncode==0 and result.stdout==b'200'
        except (OSError,subprocess.TimeoutExpired):
            return False
    if not checks:return []
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        failed=[item for item,ok in zip(checks,pool.map(probe,checks)) if not ok]
        if failed:
            failed=[item for item,ok in zip(failed,pool.map(probe,failed)) if not ok]
    return [item for item in checks if item not in failed]


def check_services(checks):
    healthy=reachable_services(checks)
    failed=[item for item in checks if item not in healthy]
    if failed:
        raise RuntimeError('服务可达性检查失败，已取消本次分流变更。'+ '、'.join(item['label'] for item in failed))
