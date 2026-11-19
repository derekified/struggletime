#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""StruggleTime menu bar / system tray daemon (linux, macos, windows).

  pip install -r daemon/tray-requirements.txt
  python3 daemon/tray.py

Lives in the menu bar (macos), system tray (windows) or appindicator
area (linux). Runs the same daemon.py the cli and gui run, shows
today's weighted hours next to the icon where the platform allows it,
and puts start/stop, dashboard and inspect in the icon menu. Settings
are shared with the gui (~/.aitime/gui.json).
"""
import importlib.util, json, subprocess, sys, threading, time, urllib.request, webbrowser
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("st_gui", HERE / "gui.py")
st_gui = importlib.util.module_from_spec(spec); spec.loader.exec_module(st_gui)

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:
    pystray = None

INSTALL_HINT = ("tray needs two small packages:\n  pip install -r daemon/tray-requirements.txt\n"
                "linux also needs a tray backend, e.g. sudo apt install gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1\n"
                "some linux desktops (gnome without the appindicator extension) have no tray area at all; use the gui there instead.")

def pending_count(repo):
    try: return len(json.loads((Path(repo).expanduser() / ".aitime-ledger.json").read_text()).get("pending", []))
    except Exception: return 0

def weighted_title(hours, tracking):
    return f"{hours:.1f}h" if tracking else ""

def make_icon(tracking):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(13, 12, 10, 255))
    d.arc([16, 16, 48, 48], start=40, end=330, fill=(215, 255, 106, 255) if tracking else (87, 83, 76, 255), width=6)
    d.ellipse([28, 28, 36, 36], fill=(255, 140, 55, 255) if tracking else (87, 83, 76, 255))
    return img

class Tray:
    def __init__(self, overrides):
        self.s = st_gui.load_settings()
        self.s.update({k: v for k, v in overrides.items() if v})
        self.proc, self.hours, self._icon_state = None, 0.0, None
        self.icon = pystray.Icon("struggletime", make_icon(False), "struggletime", self._menu())
        self._auto_start()

    def tracking(self): return self.proc is not None and self.proc.poll() is None

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem(lambda _: self._status(), None, enabled=False),
            pystray.MenuItem("start tracking", self._start, visible=lambda _: not self.tracking()),
            pystray.MenuItem("stop tracking", self._stop, visible=lambda _: self.tracking()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("open dashboard", lambda _: webbrowser.open(self.s["server"].rstrip("/") + "/"), default=True),
            pystray.MenuItem("inspect what would be sent", self._inspect),
            pystray.MenuItem("settings…", self._settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("quit", self._quit))

    def _status(self):
        if self.tracking():
            return f"● tracking {Path(self.s['repo']).name} as @{self.s['handle']} · {self.hours:.2f}h weighted · {pending_count(self.s['repo'])} pending"
        return "○ not tracking"

    def _auto_start(self):
        if st_gui.validate(self.s): self._notify("not set up yet. open settings… from the tray menu."); return
        self._spawn()

    def _spawn(self):
        save = dict(self.s); save["bucket_mins"] = int(save.get("bucket_mins", 5))
        st_gui.save_settings(save)
        log = open(st_gui.HOME / "daemon.log", "a")
        self.proc = subprocess.Popen(st_gui.daemon_cmd(self.s), stdout=log, stderr=log, stdin=subprocess.DEVNULL)
        self._notify(f"tracking {self.s['repo']} as @{self.s['handle']}")

    def _start(self, *_): self._spawn(); self._refresh()
    def _stop(self, *_):
        if self.tracking(): self.proc.terminate()
        self.proc = None; self._notify("stopped. sealed buckets stay in the repo ledger."); self._refresh()

    def _inspect(self, *_):
        r = subprocess.run([sys.executable, str(HERE / "inspect.py"), self.s["repo"]], capture_output=True, text=True)
        first = (r.stdout or r.stderr).strip().splitlines()
        self._notify(first[1] if len(first) > 1 else "nothing to inspect yet")

    def _settings(self, *_):
        subprocess.Popen([sys.executable, str(HERE / "gui.py")])
        self._notify("after saving settings there, quit and reopen the tray to use them.")

    def _quit(self, *_):
        if self.tracking(): self.proc.terminate()
        self.icon.stop()

    def _notify(self, msg):
        try: self.icon.notify(msg, "struggletime")
        except Exception: pass

    def _refresh(self):
        state = self.tracking()
        if state != self._icon_state:
            self._icon_state = state; self.icon.icon = make_icon(state)
        self.icon.title = weighted_title(self.hours, state)

    def _poll(self):
        while True:
            time.sleep(15)
            if self.tracking() and self.s.get("handle"):
                try:
                    req = urllib.request.Request(self.s["server"].rstrip("/") + "/v1/users/" + self.s["handle"], headers={"X-Handle": self.s["handle"]})
                    with urllib.request.urlopen(req, timeout=4) as x: self.hours = json.loads(x.read()).get("weighted_hours", 0.0)
                except Exception: pass
            if self.proc is not None and not self.tracking():
                self.proc = None; self._notify("daemon stopped unexpectedly. check ~/.aitime/daemon.log")
            self._refresh()
            try: self.icon.update_menu()
            except Exception: pass

    def run(self):
        threading.Thread(target=self._poll, daemon=True).start()
        self.icon.run()

def main():
    import argparse
    ap = argparse.ArgumentParser(prog="struggletime-tray")
    ap.add_argument("--repo", default=""); ap.add_argument("--user", default=""); ap.add_argument("--server", default="")
    ap.add_argument("--check", action="store_true", help="verify tray dependencies on this machine, then exit")
    a = ap.parse_args()
    if pystray is None: print(INSTALL_HINT); return 1
    if a.check:
        make_icon(True).save("/tmp/struggletime-tray-check.png")
        print(f"tray ok: pystray backend available, icon renders"); return 0
    Tray({"repo": a.repo, "handle": a.user, "server": a.server}).run(); return 0

if __name__ == "__main__":
    sys.exit(main())
