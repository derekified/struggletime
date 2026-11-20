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
import importlib.util, json, os, signal, subprocess, sys, threading, time, urllib.error, urllib.request, webbrowser
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

def api_json(server, method, path, token=None, body=None, timeout=12):
    url = server.rstrip("/") + path
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return 0, {"error": str(e)}

def prompt_text(title, prompt):
    """Small tk prompt when available; otherwise None (caller uses stdin/headless)."""
    if st_gui.tk is None or not has_display():
        return None
    try:
        root = st_gui.tk.Tk()
        root.withdraw()
        from tkinter import simpledialog
        val = simpledialog.askstring(title, prompt, parent=root)
        root.destroy()
        return (val or "").strip()
    except Exception:
        return None

def git_remote_url(repo):
    try:
        r = subprocess.run(
            ["git", "-C", str(Path(repo).expanduser()), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        return (r.stdout or "").strip() or None
    except Exception:
        return None

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
        if not self.s.get("project") and rt.get("project"): self.s["project"] = rt["project"]
        self.proc = None
        self.hours = 0.0
        self.auth = {"ok": False, "handle": None, "needs_handle": False}
        self._stop = False
        self.refresh_auth()

    def persist(self):
        save = dict(self.s)
        save["bucket_mins"] = int(save.get("bucket_mins") or 5)
        st_gui.save_settings(save)

    def tracking(self):
        return self.proc is not None and self.proc.poll() is None

    def paused(self):
        return dcfg.is_paused()

    def signed_in(self):
        return bool(self.auth.get("ok") and self.auth.get("handle"))

    def status_line(self):
        repo = Path(self.s.get("repo") or ".").name
        proj = self.s.get("project") or repo
        if not self.s.get("token"):
            auth = "signed out"
        elif self.auth.get("needs_handle"):
            auth = "signed in · claim a handle"
        elif self.signed_in():
            auth = f"@{self.auth['handle']}"
        else:
            auth = "auth expired"
        if self.tracking():
            state = "paused" if self.paused() else "active"
            return f"Status: {state.capitalize()} — {proj} · {auth} · {format_duration(self.hours)} today · {pending_count(self.s.get('repo'))} pending"
        return f"Status: No active session · {auth} · {format_duration(self.hours)} today"

    def refresh_auth(self):
        token = self.s.get("token") or ""
        if not token:
            self.auth = {"ok": False, "handle": None, "needs_handle": False}
            return self.auth
        code, data = api_json(self.s["server"], "GET", "/v1/auth/me", token=token)
        if code == 200 and data.get("handle"):
            self.auth = {"ok": True, "handle": data["handle"], "needs_handle": False}
            self.s["handle"] = data["handle"]
            self.persist()
        elif code == 200 and data.get("needs_handle"):
            self.auth = {"ok": True, "handle": None, "needs_handle": True}
        else:
            self.auth = {"ok": False, "handle": None, "needs_handle": False}
        return self.auth

    def open_login(self):
        """Open Slack OAuth in the browser (same flow as the dashboard)."""
        url = self.s["server"].rstrip("/") + "/v1/auth/slack/start"
        webbrowser.open(url)
        return "opened Slack sign-in — after the browser finishes, use “paste auth code / token…”"

    def apply_auth_secret(self, raw):
        """Accept a one-time ?auth= code or a session bearer token; save to gui.json."""
        raw = (raw or "").strip()
        if not raw:
            return False, "nothing pasted"
        # auth codes from /login?auth=… are exchanged; long tokens are used as bearer
        if len(raw) < 80 and " " not in raw:
            code, data = api_json(self.s["server"], "POST", "/v1/auth/session", body={"code": raw})
            if code == 200 and data.get("token"):
                self.s["token"] = data["token"]
                if data.get("handle"):
                    self.s["handle"] = data["handle"]
                self.persist()
                self.refresh_auth()
                if data.get("needs_handle"):
                    return True, "signed in — claim a handle on the dashboard, then reopen the tray"
                return True, f"signed in as @{self.s.get('handle')}"
            # fall through: maybe it was already a short token
        self.s["token"] = raw
        self.persist()
        self.refresh_auth()
        if self.signed_in():
            return True, f"signed in as @{self.auth['handle']}"
        if self.auth.get("needs_handle"):
            return True, "token saved — claim a handle on the dashboard"
        return False, "token rejected — sign in again"

    def sign_out(self):
        token = self.s.get("token")
        if token:
            api_json(self.s["server"], "POST", "/v1/auth/logout", token=token)
        self.s["token"] = ""
        self.persist()
        self.refresh_auth()
        return "signed out"

    def link_project(self, project_id=None):
        """Create/claim project and attach this repo's git remote via existing APIs."""
        if not self.signed_in():
            return False, "sign in first"
        repo = self.s.get("repo")
        if not repo or not Path(repo).expanduser().is_dir():
            return False, "pick a repo in settings first"
        pid = (project_id or self.s.get("project") or Path(repo).expanduser().name).strip()
        remote = git_remote_url(repo)
        name = pid
        code, data = api_json(
            self.s["server"], "POST", "/v1/projects",
            token=self.s["token"],
            body={"id": pid, "name": name, "repo_url": remote},
        )
        if code == 409:
            code, data = api_json(
                self.s["server"], "POST", f"/v1/projects/{pid}/edit",
                token=self.s["token"],
                body={"name": name, "repo_url": remote},
            )
        if code not in (200, 201):
            return False, (data.get("error") if isinstance(data, dict) else None) or f"link failed ({code})"
        self.s["project"] = pid
        self.persist()
        # restart tracker against the linked project id if already running
        if self.tracking():
            self.stop_daemon()
            self.spawn()
        linked = remote or "(no git remote — project id only)"
        return True, f"linked {pid} → {linked}"

    def spawn(self):
        errs = st_gui.validate(self.s)
        if errs:
            return False, "; ".join(errs)
        self.persist()
        dcfg.HOME.mkdir(parents=True, exist_ok=True)
        log = open(dcfg.HOME / "daemon.log", "a")
        cmd = st_gui.daemon_cmd(self.s)
        if self.s.get("token"):
            cmd += ["--token", self.s["token"]]
        self.proc = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL)
        who = self.s.get("handle") or "?"
        return True, f"tracking {self.s['repo']} as @{who}"

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
        self.refresh_auth()
        handle = self.auth.get("handle") or self.s.get("handle")
        if not handle:
            return
        try:
            headers = {}
            if self.s.get("token"):
                headers["Authorization"] = "Bearer " + self.s["token"]
            req = urllib.request.Request(
                self.s["server"].rstrip("/") + "/v1/users/" + handle,
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=4) as x:
                d = json.loads(x.read())
                self.hours = d.get("weighted_today_hours", d.get("weighted_hours", 0.0))
        except Exception:
            pass

    def open_dashboard(self):
        webbrowser.open(self.s["server"].rstrip("/") + ("/home" if self.signed_in() else "/login"))

    def open_config(self):
        dcfg.HOME.mkdir(parents=True, exist_ok=True)
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
        # StruggleTime-style status + core actions; Login / Linking added for aitime
        return pystray.Menu(
            pystray.MenuItem(lambda _: self.status_line(), None, enabled=False),
            pystray.MenuItem(lambda _: f"Today: {format_duration(self.hours)}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("sign in with slack…", self._login, visible=lambda _: not self.signed_in()),
            pystray.MenuItem("paste auth code / token…", self._paste_auth, visible=lambda _: not self.signed_in() or self.auth.get("needs_handle")),
            pystray.MenuItem("sign out", self._sign_out, visible=lambda _: bool(self.s.get("token"))),
            pystray.MenuItem("link project / repo…", self._link, visible=lambda _: self.signed_in()),
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

    def _login(self, *_):
        self._notify(self.open_login())

    def _paste_auth(self, *_):
        val = prompt_text("StruggleTime sign-in", "Paste the ?auth= code from the browser URL, or your session token:")
        if val is None:
            self._notify("open settings or run tray --headless to paste a token")
            return
        ok, msg = self.apply_auth_secret(val)
        self._notify(msg)
        self._refresh()

    def _sign_out(self, *_):
        self._notify(self.sign_out()); self._refresh()

    def _link(self, *_):
        suggested = self.s.get("project") or Path(self.s.get("repo") or ".").name
        pid = prompt_text("Link project / repo", f"Project id to create or update (default {suggested}):")
        if pid is None:
            pid = ""
        ok, msg = self.link_project(pid or None)
        self._notify(msg)
        self._refresh()

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
    print("commands: status | login | token <code|token> | logout | link [project] | start | stop | pause | resume | sync | dashboard | config | quit", flush=True)
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
        elif line == "login":
            print(core.open_login(), flush=True)
        elif line.startswith("token ") or line.startswith("auth "):
            ok, msg = core.apply_auth_secret(line.split(None, 1)[1])
            print(msg, flush=True)
        elif line == "logout":
            print(core.sign_out(), flush=True)
        elif line == "link" or line.startswith("link "):
            parts = line.split(None, 1)
            ok, msg = core.link_project(parts[1] if len(parts) > 1 else None)
            print(msg, flush=True)
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
