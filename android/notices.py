"""Collect third-party notices from the exact Go dependencies included in the Android binary."""
from pathlib import Path
import subprocess

def go(*args):
    return subprocess.check_output(["go", *args], text=True, cwd=Path(__file__).parent / "core").strip()

modules = go("list", "-mod=readonly", "-deps", "-f", "{{with .Module}}{{.Path}} {{.Dir}}{{end}}", ".", "golang.org/x/mobile/bind")
notices = [("Go runtime", Path(go("env", "GOROOT")) / "LICENSE")]
for row in sorted(set(modules.splitlines()) - {""}):
    name, directory = row.split(" ", 1)
    if name.startswith("github.com/Maotiannan/"):
        continue
    candidates = sorted(p for p in Path(directory).iterdir()
                        if p.is_file() and p.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")))
    if not candidates:
        raise ValueError("Missing license for " + name)
    notices.extend((name, f) for f in candidates)
out = Path(__file__).parent / "app/src/main/assets/third-party.txt"
out.write_text("\n\n".join(title + "\n" + path.read_text() for title, path in notices), encoding="utf-8")
