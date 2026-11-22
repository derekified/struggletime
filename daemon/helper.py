#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""CLI twin of the StruggleTime tray.

Same settings file and daemon control as tray.py / TrayCore:

  struggletime-helper status
  struggletime-helper start [--repo PATH] [--project ID]
  struggletime-helper stop | pause | resume | sync
  struggletime-helper apikey <st_…>
  struggletime-helper clear-key
  struggletime-helper link [project] | create
  struggletime-helper dashboard | config | logs
  struggletime-helper server [URL]

Defaults come from ~/.config/struggletime/settings.json (server normalized
to the stable sslip.io public URL — never ephemeral Cloudflare tunnels).
"""
import argparse, os, signal, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).parent
sys.path = [p for p in sys.path if Path(p or ".").resolve() != HERE.resolve()]
sys.path.insert(0, str(HERE))
import config as dcfg
import tray as st_tray

# ── terminal chrome (TTY only; plain text when piped) ───────────────────
def _tty():
    return bool(getattr(sys.stdout, "isatty", lambda: False)())

def _c(code, text):
    if not _tty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"

BANNER = r"""
   ____  _                      _      _____ _
  / ___|| |_ _ __ _   _  __ _  | | ___|_   _(_)_ __ ___   ___
  \___ \| __| '__| | | |/ _` | | |/ _ \ | | | | '_ ` _ \ / _ \
   ___) | |_| |  | |_| | (_| | | |  __/ | | | | | | | | |  __/
  |____/ \__|_|   \__,_|\__, | |_|\___| |_| |_|_| |_| |_|\___|
                        |___/
"""

def print_banner():
    if not _tty() or os.environ.get("STRUGGLETIME_NO_BANNER"):
        return
    sys.stdout.write(_c("1;32", BANNER.lstrip("\n")))
    sys.stdout.write(_c("2", "  ⏱️  StruggleTime helper  ·  productive struggle, not activity\n\n"))

def step(msg, emoji="⏱️"):
    """StruggleTime step line (stopwatch mark on TTY)."""
    if _tty() and not os.environ.get("NO_COLOR"):
        mark = emoji if emoji else "⏱️"
        print(f"{mark}  {_c('1', msg)}")
    else:
        print(f"==> {msg}")

def ok(msg):
    print(f"{'✅' if _tty() else 'OK'}  {msg}")

def warn(msg):
    print(f"{'⚠️' if _tty() else 'WARN'}  {msg}")

def err(msg):
    print(f"{'❌' if _tty() else 'ERR'}  {msg}", file=sys.stderr)

def info(msg):
    print(f"{'ℹ️' if _tty() else '--'}  {msg}")

def _core(a=None):
    overrides = {}
    if a is not None:
        if getattr(a, "repo", None): overrides["repo"] = a.repo
        if getattr(a, "user", None): overrides["handle"] = a.user
        if getattr(a, "server", None): overrides["server"] = dcfg.normalize_server(a.server)
        if getattr(a, "token", None): overrides["token"] = a.token
        if getattr(a, "project", None): overrides["project"] = a.project
    core = st_tray.TrayCore(overrides)
    core.s["server"] = dcfg.normalize_server(core.s.get("server"))
    return core

def cmd_status(a):
    print_banner()
    step("Reading tray status", "⏱️")
    core = _core(a)
    core.fetch_hours()
    print(core.status_line())
    pid = dcfg.read_daemon_pid()
    if pid:
        ok(f"daemon running (pid {pid})")
        if dcfg.DAEMON_ARGS.exists():
            info(dcfg.DAEMON_ARGS.read_text().strip())
    else:
        warn("daemon not running")
    info(f"server {core.s.get('server')}")
    return 0 if pid else 1

def cmd_start(a):
    print_banner()
    step("Starting StruggleTime daemon", "⏱️")
    core = _core(a)
    if a.bucket_secs:
        core.s["bucket_mins"] = max(1, int(a.bucket_secs) // 60)
    if dcfg.read_daemon_pid():
        info(f"already running (pid {dcfg.read_daemon_pid()})")
        return 0
    # Legacy/offline: allow start with --repo/--user and no API key (tests + local)
    if not core.s.get("token"):
        repo = (a.repo or core.s.get("repo") or "").strip()
        user = (a.user or core.s.get("handle") or "").strip()
        if not repo or not Path(repo).expanduser().is_dir():
            err("set an API key first (struggletime-helper apikey <st_…>) or pass --repo")
            return 1
        if not user:
            err("pass --user HANDLE or set an API key so the handle is known")
            return 1
        core.s["repo"] = str(Path(repo).expanduser())
        core.s["handle"] = user
        core.s["server"] = dcfg.normalize_server(a.server or core.s.get("server"))
        if a.project:
            core.s["project"] = a.project
        # Persist so multi-project watch list includes this root
        core.s = dcfg.upsert_linked_project(core.s, project_id=core.s.get("project") or "", local_path=core.s["repo"])
        dcfg.save_settings(core.s)
        dcfg.HOME.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, str(HERE / "daemon.py"),
            "--repo", core.s["repo"], "--user", user,
            "--server", core.s["server"],
            "--bucket-secs", str(a.bucket_secs or max(1, int(core.s.get("bucket_mins") or 5) * 60)),
        ]
        if core.s.get("project"):
            cmd += ["--project", core.s["project"]]
        if a.token:
            cmd += ["--token", a.token]
        with open(dcfg.DAEMON_LOG, "a") as log:
            p = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        dcfg.write_daemon_pid(p.pid, cmd)
        ok(f"started (pid {p.pid})")
        info(f"logging to {dcfg.DAEMON_LOG}")
        return 0
    ok_spawn, msg = core.spawn()
    (ok if ok_spawn else err)(msg)
    return 0 if ok_spawn else 1

def cmd_stop(a):
    print_banner()
    step("Stopping daemon", "🛑")
    core = _core(a)
    pid = core.stop_daemon()
    if pid:
        ok(f"stopped (pid {pid})")
    else:
        warn("not running")
        dcfg.clear_daemon_pid()
    return 0

def cmd_pause(a):
    print_banner()
    step("Pausing seals (watcher stays up)", "⏸️")
    _core(a).pause()
    ok("paused — buckets will not seal until you resume")
    return 0

def cmd_resume(a):
    print_banner()
    step("Resuming seals", "▶️")
    _core(a).resume()
    ok("resumed")
    return 0

def cmd_sync(a):
    print_banner()
    step("Flushing pending ledger buckets", "🔒")
    ok_flush, msg = _core(a).flush()
    (ok if ok_flush else err)(msg)
    return 0 if ok_flush else 1

def cmd_apikey(a):
    print_banner()
    step("Saving API key", "🔑")
    ok_key, msg = _core(a).set_api_key(a.key)
    (ok if ok_key else err)(msg)
    return 0 if ok_key else 1

def cmd_clear_key(a):
    print_banner()
    step("Clearing API key", "🧹")
    print(_core(a).clear_api_key())
    return 0

def cmd_link(a):
    print_banner()
    step("Linking local directory to a project", "🔗")
    core = _core(a)
    if a.project:
        ok_link, msg = core.link_project(a.project, local_path=a.repo or None)
    elif sys.stdin.isatty() and not a.repo:
        ok_link, msg = core.link_project_interactive(prefer_create=False)
    else:
        ok_link, msg = core.link_project(None, local_path=a.repo or None)
    (ok if ok_link else err)(msg)
    return 0 if ok_link else 1

def cmd_create(a):
    print_banner()
    step("Create / link project", "📦")
    ok_c, msg = _core(a).link_project_interactive(prefer_create=True)
    (ok if ok_c else err)(msg)
    return 0 if ok_c else 1

def cmd_dashboard(a):
    print_banner()
    step("Opening dashboard", "🌐")
    try:
        _core(a).open_dashboard()
        ok("opened dashboard")
        return 0
    except Exception as e:
        err(f"could not open browser: {e}")
        return 1

def cmd_config(a):
    print_banner()
    step("Config path", "⚙️")
    print(_core(a).open_config())
    return 0

def cmd_logs(a):
    print_banner()
    step(f"Last {a.lines} log lines", "📜")
    if not dcfg.DAEMON_LOG.exists():
        warn("no logs yet")
        return 0
    lines = dcfg.DAEMON_LOG.read_text(errors="ignore").splitlines()
    print("\n".join(lines[-a.lines:]))
    return 0

def cmd_server(a):
    """Show or set the public server URL (always normalized; tunnels rejected)."""
    print_banner()
    core = _core(a)
    if a.url:
        step("Updating public server URL", "🌍")
        core.s["server"] = dcfg.normalize_server(a.url)
        core.persist()
        ok(f"server set to {core.s['server']}")
        if a.url.rstrip("/") != core.s["server"]:
            warn("ephemeral tunnel / invalid URL was replaced with the stable public host")
        return 0
    step("Current server", "🌍")
    print(dcfg.normalize_server(core.s.get("server")))
    return 0

def main():
    dcfg.ensure_public_server()
    ap = argparse.ArgumentParser(
        prog="struggletime-helper",
        description="CLI twin of the StruggleTime tray (same settings + daemon control)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--repo", default="")
        sp.add_argument("--user", default="")
        sp.add_argument("--server", default="")
        sp.add_argument("--token", default="")
        sp.add_argument("--project", default="")
        return sp

    st = add_common(sub.add_parser("start", help="start tracking (same as tray Start)"))
    st.add_argument("--bucket-secs", type=int, default=None)
    st.set_defaults(fn=cmd_start)

    for name, fn, help_ in (
        ("stop", cmd_stop, "stop the tracker daemon"),
        ("status", cmd_status, "show tray-equivalent status"),
        ("pause", cmd_pause, "pause sealing (still watching)"),
        ("resume", cmd_resume, "resume sealing"),
        ("sync", cmd_sync, "flush pending ledger buckets"),
        ("dashboard", cmd_dashboard, "open the web dashboard"),
        ("config", cmd_config, "print settings path / open config"),
        ("clear-key", cmd_clear_key, "remove the API key from this machine"),
        ("create", cmd_create, "create/link a project interactively"),
    ):
        sp = add_common(sub.add_parser(name, help=help_))
        sp.set_defaults(fn=fn)

    lg = add_common(sub.add_parser("logs", help="tail daemon.log"))
    lg.add_argument("--lines", type=int, default=30)
    lg.set_defaults(fn=cmd_logs)

    ak = add_common(sub.add_parser("apikey", help="save a StruggleTime API key (st_…)"))
    ak.add_argument("key")
    ak.set_defaults(fn=cmd_apikey)

    lk = add_common(sub.add_parser("link", help="link a local directory to a project"))
    lk.set_defaults(fn=cmd_link)

    sv = add_common(sub.add_parser("server", help="show or set the public server URL"))
    sv.add_argument("url", nargs="?", default="")
    sv.set_defaults(fn=cmd_server)

    a = ap.parse_args()
    return a.fn(a)

if __name__ == "__main__":
    sys.exit(main() or 0)
