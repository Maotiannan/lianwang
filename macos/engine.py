"""Locate the original, signed MClash helper without depending on Launch Services."""
from pathlib import Path
import subprocess
import time


INSTALLED_APP = Path('/Applications/联网.app')
LEGACY_ENGINE = Path('/Applications/MClash.app')


def bundled_engine(resources):
    return Path(resources).parent / 'Helpers/MClash.app'


def locate_engine(resources=Path(__file__).resolve().parent):
    resources = Path(resources)
    # An installed or staged bundle must use its own helper, even if it is
    # missing. Falling back could silently launch a different installation.
    if resources.parent.parent.suffix == '.app':
        return bundled_engine(resources)
    installed = bundled_engine(INSTALLED_APP / 'Contents/Resources')
    return installed if installed.is_dir() else LEGACY_ENGINE


ENGINE = locate_engine()
CTL = str(ENGINE / 'Contents/Helpers/mclashctl')
XRAY = str(ENGINE / 'Contents/Resources/Core/mclash-xray')
LAUNCHER = ENGINE.parent.parent / 'MacOS/EngineLauncher'


def ensure_running(ctl=CTL):
    """Keep launch policy in our wrapper; never let the vendor CLI reopen it."""
    helper = Path(ctl).parents[2]
    subprocess.run([str(LAUNCHER), str(helper)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        try:
            ready = subprocess.run([str(ctl), 'core.status', '--no-launch', '--timeout', '2'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
            if ready.returncode == 0:
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(0.2)
    raise RuntimeError('内置后台启动后未能就绪。')


def is_router(path):
    target = Path(path).resolve()
    roots = (ENGINE, INSTALLED_APP, LEGACY_ENGINE,
             Path('/Applications/网络出口.app'), Path('/Applications/Clash Verge.app'),
             Path('/Library/Application Support/clash-verge-service'))
    return any(target == root.resolve() or root.resolve() in target.parents for root in roots)
