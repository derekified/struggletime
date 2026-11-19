#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Manage the struggletime daemon as a background helper.

  python3 daemon/helper.py start --repo ~/my-project --user you
  python3 daemon/helper.py status
  python3 daemon/helper.py logs
  python3 daemon/helper.py stop
"""
import argparse, os, signal, subprocess, sys, time
from pathlib import Path

HOME = Path(os.environ.get("AITIME_HOME", Path.home() / ".aitime"))
PIDF, LOGF, CFG = HOME / "daemon.pid", HOME / "daemon.log", HOME / "daemon.args"

def alive(pid):
    try: os.kill(pid, 0); return True
    except Exception: return False

def read_pid():
    try: return int(PIDF.read_text().strip())
    except Exception: return None

def cmd_start(a):
    HOME.mkdir(parents=True, exist_ok=True)
    pid = read_pid()
    if pid and alive(pid): print(f"already running (pid {pid})"); return 0
    args = [sys.executable, str(Path(__file__).parent / "daemon.py"), "--repo", a.repo, "--user", a.user,
            "--server", a.server, "--bucket-secs", str(a.bucket_secs)]
    if a.project: args += ["--project", a.project]
    CFG.write_text("\n".join(args))
    with open(LOGF, "a") as log:
        p = subprocess.Popen(args, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    PIDF.write_text(str(p.pid)); print(f"started (pid {p.pid}) logging to {LOGF}"); return 0

def cmd_stop(a):
    pid = read_pid()
    if not pid or not alive(pid): print("not running"); PIDF.unlink(missing_ok=True); return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        if not alive(pid): break
        time.sleep(0.1)
    if alive(pid): os.kill(pid, signal.SIGKILL)
    PIDF.unlink(missing_ok=True); print(f"stopped (pid {pid})"); return 0

def cmd_status(a):
    pid = read_pid()
    if pid and alive(pid):
        print(f"running (pid {pid})"); print(CFG.read_text() if CFG.exists() else ""); return 0
    print("not running"); return 1

def cmd_logs(a):
    if not LOGF.exists(): print("no logs yet"); return 0
    lines = LOGF.read_text(errors="ignore").splitlines()
    print("\n".join(lines[-a.lines:])); return 0

def main():
    ap = argparse.ArgumentParser(prog="struggletime-helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("start"); st.add_argument("--repo", required=True); st.add_argument("--user", default="")
    st.add_argument("--server", default=os.environ.get("AITIME_SERVER", "http://127.0.0.1:18765"))
    st.add_argument("--project", default=None); st.add_argument("--bucket-secs", type=int, default=300); st.set_defaults(fn=cmd_start)
    for name, fn in (("stop", cmd_stop), ("status", cmd_status)):
        sp = sub.add_parser(name); sp.set_defaults(fn=fn)
    lg = sub.add_parser("logs"); lg.add_argument("--lines", type=int, default=30); lg.set_defaults(fn=cmd_logs)
    a = ap.parse_args(); return a.fn(a)

if __name__ == "__main__":
    sys.exit(main())
