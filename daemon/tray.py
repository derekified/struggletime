#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""StruggleTime menu bar / system tray daemon (linux, macos, windows).

  pip install -r daemon/tray-requirements.txt
  python3 daemon/tray.py

StruggleTime-inspired tray lifecycle: live status + today's weighted time,
start/stop, open dashboard, inspect, settings, quit. Falls back to a
headless CLI loop when no display / AppIndicator is available.
Settings come from ~/.aitime/gui.json only (custom aitime architecture).
"""
import importlib.util, json, os, signal, subprocess, sys, threading, time, urllib.request, webbrowser
from pathlib import Path

HERE = Path(__file__).parent
sys.path = [p for p in sys.path if Path(p or ".").resolve() != HERE.resolve()]
sys.path.insert(0, str(HERE))
import config as dcfg

spec = importlib.util.spec_from_file_location("st_gui", HERE / "gui.py")
st_gui = importlib.util.module_from_spec(spec); spec.loader.exec_module(st_gui)

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:
    pystray = None

INSTALL_HINT = (
    "tray needs two small packages:\n"
    "  pip install -r daemon/tray-requirements.txt\n"
    "linux also needs a tray backend, e.g.\n"
    "  sudo apt install gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1\n"
    "some desktops have no tray area; use `python3 daemon/gui.py` or headless mode."
)

def has_display():
    if sys.platform == "darwin":
        return True
    if sys.platform.startswith("win"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

def pending_count(repo):
    try:
        return len(json.loads((Path(repo).expanduser() / ".aitime-ledger.json").read_text()).get("pending", []))
    except Exception:
        return 0

def format_duration(hours):
    mins = int(round((hours or 0) * 60)); h, m = divmod(mins, 60)
    return f"{h}h {m:02d}m" if h and m else (f"{h}h" if h else f"{m}m")

def weighted_title(hours, tracking=None):
    base = format_duration(hours)
    if tracking is False:
        return base
    if tracking is True:
        return base
    return base

def make_icon(tracking, paused=False):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(13, 12, 10, 255))
    if paused:
        ring = (255, 140, 55, 255)
    elif tracking:
        ring = (215, 255, 106, 255)
    else:
        ring = (87, 83, 76, 255)
    d.arc([16, 16, 48, 48], start=40, end=330, fill=ring, width=6)
    d.ellipse([28, 28, 36, 36], fill=(255, 140, 55, 255) if tracking and not paused else (87, 83, 76, 255))
    return img

class TrayCore:
    """Shared daemon control used by tray UI and headless CLI."""

    def __init__(self, overrides):
        self.s = st_gui.load_settings()
        self.s.update({k: v for k, v in overrides.items() if v})
        rt = dcfg.resolve_runtime(
            cli_server=overrides.get("server") or "",
            cli_token=overrides.get("token") or self.s.get("token") or "",
            cli_user=overrides.get("handle") or "",
            cli_repo=overrides.get("repo") or "",
        )
        if not self.s.get("server"): self.s["server"] = rt["server"]
        if not self.s.get("token") and rt.get("token"): self.s["token"] = rt["token"]
        if not self.s.get("handle"): self.s["handle"] = rt["handle"]
        if not self.s.get("repo"): self.s["repo"] = rt["repo"]
        self.proc = None
        self.hours = 0.0
        self._stop = False

    def tracking(self):
        return self.proc is not None and self.proc.poll() is None

    def paused(self):
        return dcfg.is_paused()

    def status_line(self):
        repo = Path(self.s.get("repo") or ".").name
        handle = self.s.get("handle") or "?"
        if self.tracking():
            state = "paused" if self.paused() else "active"
            return f"Status: {state.capitalize()} — {repo} @{handle} · {format_duration(self.hours)} today · {pending_count(self.s['repo'])} pending"
        return f"Status: No active session · {format_duration(self.hours)} today"

    def spawn(self):
        errs = st_gui.validate(self.s)
        if errs:
            return False, "; ".join(errs)
        save = dict(self.s); save["bucket_mins"] = int(save.get("bucket_mins", 5))
        st_gui.save_settings(save)
        dcfg.HOME.mkdir(parents=True, exist_ok=True)
        log = open(dcfg.HOME / "daemon.log", "a")
        cmd = st_gui.daemon_cmd(self.s)
        if self.s.get("token"):
            cmd += ["--token", self.s["token"]]
        self.proc = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL)
        return True, f"tracking {self.s['repo']} as @{self.s['handle']}"

    def stop_daemon(self):
        if self.tracking():
            self.proc.terminate()
            try: self.proc.wait(timeout=5)
            except Exception: self.proc.kill()
        self.proc = None

    def pause(self):
        dcfg.set_paused(True)

    def resume(self):
        dcfg.set_paused(False)

    def flush(self):
        repo = self.s.get("repo")
        if not repo:
            return False, "no repo configured"
        cmd = [
            sys.executable, str(HERE / "daemon.py"),
            "--repo", str(Path(repo).expanduser()),
            "--server", self.s["server"].rstrip("/"),
            "--user", self.s.get("handle") or "",
            "--flush-only",
        ]
        if self.s.get("token"):
            cmd += ["--token", self.s["token"]]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        msg = (r.stdout or r.stderr or "").strip().splitlines()
        return r.returncode == 0, (msg[-1] if msg else ("flushed" if r.returncode == 0 else "flush failed"))

    def fetch_hours(self):
        if not self.s.get("handle"):
            return
        try:
            headers = {}
            if self.s.get("token"):
                headers["Authorization"] = "Bearer " + self.s["token"]
            req = urllib.request.Request(
                self.s["server"].rstrip("/") + "/v1/users/" + self.s["handle"],
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=4) as x:
                d = json.loads(x.read())
                self.hours = d.get("weighted_today_hours", d.get("weighted_hours", 0.0))
        except Exception:
            pass

    def open_dashboard(self):
        webbrowser.open(self.s["server"].rstrip("/") + "/home")

    def open_config(self):
        dcfg.HOME.mkdir(parents=True, exist_ok=True)
        # ensure settings exist; open gui when display available else print paths
        if has_display() and st_gui.tk is not None:
            subprocess.Popen([sys.executable, str(HERE / "gui.py")])
            return "opened settings window"
        return f"config: {dcfg.GUI_SETTINGS}"

class Tray(TrayCore):
    def __init__(self, overrides):
        super().__init__(overrides)
        self._icon_state = None
        self.icon = pystray.Icon("struggletime", make_icon(False), weighted_title(0), self._menu())
        self._auto_start()

    def _menu(self):
        # Docs menu: Start/Stop, Open Dashboard, Inspect, Settings, Quit (+ live status/time)
        return pystray.Menu(
            pystray.MenuItem(lambda _: self.status_line(), None, enabled=False),
            pystray.MenuItem(lambda _: f"Today: {format_duration(self.hours)}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("start tracking", self._start, visible=lambda _: not self.tracking()),
            pystray.MenuItem("stop tracking", self._stop, visible=lambda _: self.tracking()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("open dashboard", lambda *_: self.open_dashboard(), default=True),
            pystray.MenuItem("inspect what would be sent", self._inspect),
            pystray.MenuItem("settings…", self._settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("quit", self._quit),
        )

    def _auto_start(self):
        if st_gui.validate(self.s):
            self._notify("not set up yet. open config / settings… from the tray menu.")
            return
        ok, msg = self.spawn()
        self._notify(msg if ok else msg)

    def _start(self, *_):
        ok, msg = self.spawn(); self._notify(msg); self._refresh()

    def _stop(self, *_):
        self.stop_daemon()
        self._notify("stopped. sealed buckets stay in the repo ledger.")
        self._refresh()

    def _pause(self, *_):
        self.pause(); self._notify("paused"); self._refresh()

    def _resume(self, *_):
        self.resume(); self._notify("resumed"); self._refresh()

    def _flush(self, *_):
        ok, msg = self.flush(); self._notify(msg if ok else f"sync failed: {msg}")

    def _inspect(self, *_):
        r = subprocess.run([sys.executable, str(HERE / "tracker_inspect.py"), self.s["repo"]], capture_output=True, text=True)
        first = (r.stdout or r.stderr).strip().splitlines()
        self._notify(first[1] if len(first) > 1 else "nothing to inspect yet")

    def _settings(self, *_):
        self._notify(self.open_config())

    def _quit(self, *_):
        self.stop_daemon()
        self.icon.stop()

    def _notify(self, msg):
        try: self.icon.notify(msg, "struggletime")
        except Exception: print(f"[tray] {msg}", flush=True)

    def _refresh(self):
        state = (self.tracking(), self.paused())
        if state != self._icon_state:
            self._icon_state = state
            self.icon.icon = make_icon(state[0], paused=state[1])
        self.icon.title = weighted_title(self.hours, tracking=self.tracking())

    def _poll(self):
        while not self._stop:
            time.sleep(15)
            self.fetch_hours()
            if self.proc is not None and not self.tracking():
                self.proc = None
                self._notify("daemon stopped unexpectedly. check ~/.aitime/daemon.log")
            self._refresh()
            try: self.icon.update_menu()
            except Exception: pass

    def run(self):
        self.fetch_hours(); self._refresh()
        threading.Thread(target=self._poll, daemon=True).start()
        self.icon.run()

def run_headless(overrides):
    """Safe fallback when running without a tray/display server."""
    core = TrayCore(overrides)
    print("struggletime tray: headless mode (no display / pystray)", flush=True)
    print(INSTALL_HINT if pystray is None else "no DISPLAY/WAYLAND_DISPLAY — CLI control loop", flush=True)
    print("commands: status | start | stop | pause | resume | sync | dashboard | config | quit", flush=True)
    if not st_gui.validate(core.s):
        ok, msg = core.spawn()
        print(msg, flush=True)
    stop = {"v": False}

    def _sig(*_):
        stop["v"] = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        try: signal.signal(sig, _sig)
        except Exception: pass

    def poll():
        while not stop["v"]:
            time.sleep(15)
            core.fetch_hours()
            if core.proc is not None and not core.tracking():
                core.proc = None
                print("daemon stopped unexpectedly", flush=True)
            print(core.status_line(), flush=True)
    threading.Thread(target=poll, daemon=True).start()
    core.fetch_hours()
    print(core.status_line(), flush=True)

    def handle(line):
        line = (line or "").strip().lower()
        if not line:
            return False
        if line in ("q", "quit", "exit"):
            return True
        if line == "status":
            core.fetch_hours(); print(core.status_line(), flush=True)
        elif line == "start":
            print(core.spawn()[1], flush=True)
        elif line == "stop":
            core.stop_daemon(); print("stopped", flush=True)
        elif line == "pause":
            core.pause(); print("paused", flush=True)
        elif line == "resume":
            core.resume(); print("resumed", flush=True)
        elif line in ("sync", "flush"):
            print(core.flush()[1], flush=True)
        elif line == "dashboard":
            core.open_dashboard(); print("opened dashboard", flush=True)
        elif line == "config":
            print(core.open_config(), flush=True)
        else:
            print("unknown command", flush=True)
        return False

    if not sys.stdin.isatty():
        # piped/CI: consume commands then exit (service mode: no stdin → idle until signal)
        had = False
        for line in sys.stdin:
            had = True
            if handle(line):
                break
        if not had:
            while not stop["v"]:
                time.sleep(1)
        core.stop_daemon()
        return 0
    while not stop["v"]:
        try:
            line = input("tray> ")
        except EOFError:
            break
        if handle(line):
            break
    core.stop_daemon()
    return 0

def main():
    import argparse
    ap = argparse.ArgumentParser(prog="struggletime-tray")
    ap.add_argument("--repo", default="")
    ap.add_argument("--user", default="")
    ap.add_argument("--server", default="")
    ap.add_argument("--token", default="")
    ap.add_argument("--headless", action="store_true", help="force CLI mode (no tray)")
    ap.add_argument("--check", action="store_true", help="verify tray dependencies on this machine, then exit")
    a = ap.parse_args()
    overrides = {"repo": a.repo, "handle": a.user, "server": a.server, "token": a.token}

    if a.check:
        if pystray is None:
            print(INSTALL_HINT); return 1
        make_icon(True).save("/tmp/struggletime-tray-check.png")
        print("tray ok: pystray backend available, icon renders")
        if not has_display():
            print("note: no display server — runtime will use headless fallback")
        return 0

    if a.headless or pystray is None or not has_display():
        if pystray is None and not a.headless:
            print(INSTALL_HINT, flush=True)
        return run_headless(overrides)

    Tray(overrides).run()
    return 0

if __name__ == "__main__":
    sys.exit(main())
