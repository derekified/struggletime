#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""StruggleTime menu bar / system tray daemon (linux, macos, windows).

  pip install -r daemon/tray-requirements.txt
  python3 daemon/tray.py

Live status + today's weighted time, start/stop, API key linking,
open dashboard, inspect, settings, quit. Falls back to a headless CLI
when no display / AppIndicator is available.
Settings come from ~/.config/struggletime/settings.json (XDG; custom aitime).
"""
import importlib.util, json, os, signal, subprocess, sys, threading, time, traceback, urllib.request
from pathlib import Path

# Prefer AppIndicator on Linux before pystray imports a broken backend.
if sys.platform.startswith("linux") and not os.environ.get("PYSTRAY_BACKEND"):
    try:
        import gi
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            os.environ["PYSTRAY_BACKEND"] = "appindicator"
        except Exception:
            gi.require_version("AppIndicator3", "0.1")
            os.environ["PYSTRAY_BACKEND"] = "appindicator"
    except Exception:
        pass

HERE = Path(__file__).parent
sys.path = [p for p in sys.path if Path(p or ".").resolve() != HERE.resolve()]
sys.path.insert(0, str(HERE))
import config as dcfg
import browser_auth
import native_ui
import project_link

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
    "dialogs use native OS prompts (osascript / zenity / PowerShell) — no tkinter.\n"
    "some desktops (plain GNOME without AppIndicator) have no tray area;\n"
    "use `python3 daemon/gui.py` or `python3 daemon/tray.py --headless`."
)

def has_display():
    return native_ui.has_display()

def pending_count(repo):
    try:
        return len(json.loads((Path(repo).expanduser() / ".aitime-ledger.json").read_text()).get("pending", []))
    except Exception:
        return 0

def format_duration(hours):
    mins = int(round((hours or 0) * 60)); h, m = divmod(mins, 60)
    return f"{h}h {m:02d}m" if h and m else (f"{h}h" if h else f"{m}m")

def weighted_title(hours, tracking=None):
    return format_duration(hours)

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
    return browser_auth.api_json(server, method, path, token=token, body=body, timeout=timeout)

def prompt_text(title, prompt, secret=False):
    """Ask for a string via native OS dialog (no tkinter)."""
    return native_ui.prompt(title, prompt, secret=secret)

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
        self.overrides = {k: v for k, v in (overrides or {}).items() if v}
        self.s = {}
        self.proc = None
        self.hours = 0.0
        self.auth = {"ok": False, "handle": None, "needs_handle": False}
        self._stop_flag = False
        self._action_lock = threading.Lock()
        self.reload_settings()

    def reload_settings(self):
        """Re-read ~/.config/struggletime/settings.json so tray/gui/daemon stay in sync."""
        self.s = st_gui.load_settings()
        self.s.update(self.overrides)
        rt = dcfg.resolve_runtime(
            cli_server=self.overrides.get("server") or "",
            cli_token=self.overrides.get("token") or self.s.get("token") or "",
            cli_user=self.overrides.get("handle") or "",
            cli_repo=self.overrides.get("repo") or "",
        )
        if not self.s.get("server"): self.s["server"] = rt["server"]
        if not self.s.get("token") and rt.get("token"): self.s["token"] = rt["token"]
        if not self.s.get("handle"): self.s["handle"] = rt["handle"]
        if not self.s.get("repo"): self.s["repo"] = rt["repo"]
        if not self.s.get("project") and rt.get("project"): self.s["project"] = rt["project"]
        self.refresh_auth()
        return self.s

    def persist(self):
        save = dict(self.s)
        save["bucket_mins"] = int(save.get("bucket_mins") or 5)
        st_gui.save_settings(save)

    def tracking(self):
        if self.proc is not None and self.proc.poll() is None:
            return True
        # Helper CLI / another tray instance may own the daemon via shared pid file
        pid = dcfg.read_daemon_pid()
        if pid:
            return True
        self.proc = None
        return False

    def paused(self):
        return dcfg.is_paused()

    def signed_in(self):
        return bool(self.auth.get("ok") and self.auth.get("handle"))

    def status_line(self):
        repo = Path(self.s.get("repo") or ".").name
        proj = self.s.get("project") or repo
        if not self.s.get("token"):
            auth = "no API key"
        elif self.signed_in():
            auth = f"@{self.auth['handle']}"
        else:
            auth = "API key invalid"
        if self.tracking():
            state = "paused" if self.paused() else "active"
            return f"Status: {state.capitalize()} — {proj} · {auth} · {format_duration(self.hours)} today · {pending_count(self.s.get('repo'))} pending"
        return f"Status: No active session · {auth} · {format_duration(self.hours)} today"

    def refresh_auth(self):
        self.s["server"] = dcfg.normalize_server(self.s.get("server"))
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
            # Stale/revoked key — drop it so the tray prompts to re-link
            if code in (401, 403):
                self.s["token"] = ""
                self.persist()
            self.auth = {"ok": False, "handle": None, "needs_handle": False}
        return self.auth

    def browser_base(self):
        return dcfg.public_url(self.s.get("server") or "")

    def set_api_key(self, raw):
        """Validate and persist a StruggleTime API key into settings.json."""
        key = (raw or "").strip()
        if not key:
            return False, "no key entered"
        if key.startswith("<") or "ngrok" in key.lower() or "trycloudflare" in key.lower():
            return False, "that looks like a placeholder — paste the real st_… key from Settings"
        before = (self.s.get("server") or "").rstrip("/")
        self.s["server"] = dcfg.normalize_server(self.s.get("server"))
        if before and before != self.s["server"] and ("trycloudflare" in before or "ngrok" in before):
            # Keep going — key check uses the rewritten stable host
            pass
        code, data = api_json(self.s["server"], "GET", "/v1/auth/me", token=key)
        if code == 0:
            err = (data.get("error") if isinstance(data, dict) else None) or "network error"
            return False, f"cannot reach {self.s['server']}: {err}"
        if code != 200 or not data.get("handle"):
            err = (data.get("error") if isinstance(data, dict) else None) or f"rejected ({code})"
            return False, f"API key invalid: {err}"
        self.s["token"] = key
        self.s["handle"] = data["handle"]
        self.persist()
        self.refresh_auth()
        return True, f"linked as @{data['handle']}"

    def clear_api_key(self):
        self.s["token"] = ""
        self.persist()
        self.refresh_auth()
        return "API key cleared"

    def link_project(self, project_id=None, *, repo_url=None, local_path=None, create=False, name=None):
        """Programmatic link/create: optional GitHub (None = no repo) + directory → API + gui.json."""
        if not self.signed_in():
            return False, "set an API key first"
        path = local_path or self.s.get("repo") or ""
        if path:
            path = str(Path(path).expanduser())
        if not path or not Path(path).is_dir():
            return False, "choose a local directory first"
        pid = (project_id or self.s.get("project") or Path(path).name).strip()
        if repo_url is None and create is False and project_id:
            # CLI shorthand: prefer git remote when present, else leave unlinked
            gh = git_remote_url(path)
        else:
            gh = repo_url
        ok, data, err = project_link.upsert_project(
            self.s["server"], self.s["token"],
            project_id=pid, name=name or pid, repo_url=gh, local_path=path,
            create=bool(create) or not project_id,
        )
        if not ok:
            return False, err or "link failed"
        pid = (data or {}).get("id") or pid
        self.s = project_link.save_local_mapping(self.s, project_id=pid, local_path=path, repo_url=gh)
        self.reload_settings()
        if self.tracking():
            self.stop_daemon()
            self.spawn()
        return True, f"linked {pid} → {path}" + (f" · {gh}" if gh else " · no GitHub repo")

    def link_project_interactive(self, *, prefer_create=False):
        if not self.signed_in():
            return False, "set an API key first"
        was_tracking = self.tracking()
        self.s, msg = project_link.interactive_link(self.s, prefer_create=prefer_create)
        self.reload_settings()
        if was_tracking and self.s.get("repo") and self.s.get("project"):
            self.stop_daemon()
            self.spawn()
        return bool(isinstance(msg, str) and msg.startswith("linked ")), msg

    def spawn(self):
        self.reload_settings()
        self.s["server"] = dcfg.normalize_server(self.s.get("server"))
        errs = st_gui.validate(self.s)
        if errs:
            return False, "; ".join(errs)
        if not self.s.get("token"):
            return False, "set an API key first"
        if self.tracking():
            pid = dcfg.read_daemon_pid() or (self.proc.pid if self.proc else "?")
            return True, f"already running (pid {pid})"
        self.persist()
        dcfg.HOME.mkdir(parents=True, exist_ok=True)
        log = open(dcfg.DAEMON_LOG, "a")
        cmd = st_gui.daemon_cmd(self.s)
        if self.s.get("token") and "--token" not in cmd:
            cmd += ["--token", self.s["token"]]
        kwargs = {"stdout": log, "stderr": log, "stdin": subprocess.DEVNULL, "start_new_session": True}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            kwargs.pop("start_new_session", None)
        self.proc = subprocess.Popen(cmd, **kwargs)
        dcfg.write_daemon_pid(self.proc.pid, cmd)
        who = self.s.get("handle") or "?"
        n = len(dcfg.linked_projects(self.s)) or (1 if self.s.get("repo") else 0)
        return True, f"started (pid {self.proc.pid}) watching {n} project(s) as @{who}"

    def stop_daemon(self):
        pid = None
        if self.proc is not None and self.proc.poll() is None:
            pid = self.proc.pid
            self.proc.terminate()
            try: self.proc.wait(timeout=5)
            except Exception: self.proc.kill()
        else:
            pid = dcfg.read_daemon_pid()
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                    for _ in range(30):
                        if not dcfg.daemon_alive(pid):
                            break
                        time.sleep(0.1)
                    if dcfg.daemon_alive(pid):
                        os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
        self.proc = None
        dcfg.clear_daemon_pid()
        return pid

    def pause(self):
        dcfg.set_paused(True)

    def resume(self):
        dcfg.set_paused(False)

    def flush(self):
        linked = dcfg.linked_projects(self.s)
        if not linked and self.s.get("repo"):
            linked = [{"id": self.s.get("project") or "", "path": self.s["repo"]}]
        if not linked:
            return False, "no repo configured"
        cmd = [
            sys.executable, str(HERE / "daemon.py"),
            "--server", self.s["server"].rstrip("/"),
            "--user", self.s.get("handle") or "",
            "--flush-only",
        ]
        for row in linked:
            path = Path(row["path"]).expanduser()
            if path.is_dir():
                cmd += ["--repo", str(path)]
                if row.get("id"):
                    cmd += ["--project-map", f"{path}={row['id']}"]
        if self.s.get("token"):
            cmd += ["--token", self.s["token"]]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        msg = (r.stdout or r.stderr or "").strip().splitlines()
        return r.returncode == 0, (msg[-1] if msg else ("flushed" if r.returncode == 0 else "flush failed"))

    def fetch_hours(self):
        self.reload_settings()
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
        base = self.browser_base()
        path = "/settings" if self.signed_in() else "/login"
        browser_auth.open_default_browser(base + path)

    def open_config(self):
        dcfg.HOME.mkdir(parents=True, exist_ok=True)
        kwargs = {}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([sys.executable, str(HERE / "gui.py")], **kwargs)
        return "opened settings"

class Tray(TrayCore):
    def __init__(self, overrides):
        super().__init__(overrides)
        self._icon_state = None
        self._ui_lock = threading.Lock()
        self.icon = pystray.Icon(
            "struggletime",
            make_icon(False),
            weighted_title(0),
            menu=self._menu(),
        )
        self._auto_start()

    def _action(self, fn, background=False):
        """pystray-safe wrapper: catch errors, optionally run off the menu thread."""
        def handler(icon=None, item=None):
            def run():
                try:
                    with self._action_lock:
                        fn()
                except Exception as e:
                    print(f"[tray] action error: {e}\n{traceback.format_exc()}", flush=True)
                    self._notify(f"error: {e}")
                    self._refresh_ui()
            if background:
                threading.Thread(target=run, daemon=True, name="tray-action").start()
            else:
                run()
        return handler

    def _menu(self):
        # Keep items always listed (visibility lambdas are flaky on some backends).
        return pystray.Menu(
            pystray.MenuItem(lambda _item: self.status_line(), None, enabled=False),
            pystray.MenuItem(lambda _item: f"Today: {format_duration(self.hours)}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("set / link API key…", self._action(self._do_set_api_key, background=True)),
            pystray.MenuItem("get API key in browser…", self._action(self._do_open_api_key_page, background=True)),
            pystray.MenuItem("clear API key", self._action(self._do_clear_api_key), enabled=lambda _i: bool(self.s.get("token"))),
            pystray.MenuItem("link / create project…", self._action(self._do_link, background=True), enabled=lambda _i: self.signed_in()),
            pystray.MenuItem("create project…", self._action(self._do_create_project, background=True), enabled=lambda _i: self.signed_in()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("start tracking", self._action(self._do_start), enabled=lambda _i: not self.tracking()),
            pystray.MenuItem("stop tracking", self._action(self._do_stop), enabled=lambda _i: self.tracking()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("open dashboard", self._action(self._do_dashboard, background=True), default=True),
            pystray.MenuItem("inspect what would be sent", self._action(self._do_inspect, background=True)),
            pystray.MenuItem("settings…", self._action(self._do_settings, background=True)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("quit", self._action(self._do_quit)),
        )

    def _auto_start(self):
        if not self.signed_in() or st_gui.validate(self.s):
            if not self.signed_in():
                self._notify("set your StruggleTime API key from Settings → API key")
            else:
                self._notify("not set up yet — open settings… from the tray menu")
            return
        ok, msg = self.spawn()
        self._notify(msg)

    def _do_start(self):
        ok, msg = self.spawn()
        self._notify(msg)
        self._refresh_ui()

    def _do_stop(self):
        self.stop_daemon()
        self._notify("stopped. sealed buckets stay in the repo ledger.")
        self._refresh_ui()

    def _do_set_api_key(self):
        self._notify("enter your StruggleTime API key…")
        val = prompt_text(
            "StruggleTime API key",
            "Paste the API key from Settings → StruggleTime API key:",
            secret=True,
        )
        if val is None:
            self._notify("cancelled")
            return
        ok, msg = self.set_api_key(val)
        self._notify(msg)
        if not ok:
            native_ui.alert("StruggleTime", msg)
        self._refresh_ui()

    def _do_open_api_key_page(self):
        """Open Settings so the user can mint an API key on the web dashboard."""
        try:
            browser_auth.open_default_browser(self.browser_base() + "/settings")
            self._notify("opened Settings — Generate API key, then paste it here")
        except Exception as e:
            self._notify(f"could not open browser: {e}")

    def _do_clear_api_key(self):
        if not native_ui.confirm("StruggleTime", "Clear the StruggleTime API key from this machine?"):
            self._notify("cancelled")
            return
        if self.tracking():
            self.stop_daemon()
        self._notify(self.clear_api_key())
        self._refresh_ui()

    def _do_link(self):
        self._notify("link project — pick project, optional GitHub, directory…")
        ok, msg = self.link_project_interactive(prefer_create=False)
        self._notify(msg)
        if not ok and msg != "cancelled":
            native_ui.alert("StruggleTime", msg)
        self._refresh_ui()

    def _do_create_project(self):
        self._notify("create project — name, optional GitHub, directory…")
        ok, msg = self.link_project_interactive(prefer_create=True)
        self._notify(msg)
        if not ok and msg != "cancelled":
            native_ui.alert("StruggleTime", msg)
        self._refresh_ui()

    def _do_inspect(self):
        repo = self.s.get("repo") or "."
        if not Path(repo).expanduser().is_dir():
            native_ui.alert("StruggleTime", "Pick a repo in settings first.")
            self._notify("pick a repo in settings first")
            return
        r = subprocess.run(
            [sys.executable, str(HERE / "tracker_inspect.py"), str(Path(repo).expanduser())],
            capture_output=True, text=True, timeout=60,
        )
        out = (r.stdout or r.stderr or "").strip() or "nothing to inspect yet"
        lines = out.splitlines()
        summary = lines[1] if len(lines) > 1 else lines[0]
        self._notify(summary[:180])
        native_ui.alert("Inspect — what would be sent", out)

    def _do_settings(self):
        self._notify(self.open_config())

    def _do_dashboard(self):
        try:
            self.open_dashboard()
            self._notify("opened dashboard")
        except Exception as e:
            self._notify(f"could not open browser: {e}")

    def _do_quit(self):
        self._stop_flag = True
        self.stop_daemon()
        try:
            self.icon.stop()
        except Exception:
            pass

    def _notify(self, msg):
        try:
            self.icon.notify(str(msg), "struggletime")
        except Exception:
            print(f"[tray] {msg}", flush=True)

    def _refresh_ui(self):
        with self._ui_lock:
            state = (self.tracking(), self.paused())
            try:
                if state != self._icon_state:
                    self._icon_state = state
                    self.icon.icon = make_icon(state[0], paused=state[1])
                self.icon.title = weighted_title(self.hours, tracking=self.tracking())
                # rebuild menu so enabled= lambdas re-evaluate
                self.icon.menu = self._menu()
                self.icon.update_menu()
            except Exception as e:
                print(f"[tray] refresh: {e}", flush=True)

    def _poll(self):
        while not self._stop_flag:
            time.sleep(15)
            if self._stop_flag:
                break
            try:
                self.fetch_hours()
                if self.proc is not None and not self.tracking():
                    self.proc = None
                    self._notify("daemon stopped unexpectedly. check ~/.aitime/daemon.log")
                self._refresh_ui()
            except Exception as e:
                print(f"[tray] poll error: {e}", flush=True)

    def run(self):
        self.fetch_hours()
        self._refresh_ui()
        threading.Thread(target=self._poll, name="tray-poll", daemon=True).start()

        def _sig(*_):
            self._do_quit()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _sig)
            except Exception:
                pass
        self.icon.run()
        self._stop_flag = True
        self.stop_daemon()

def run_headless(overrides):
    core = TrayCore(overrides)
    print("struggletime tray: headless mode (no display / pystray)", flush=True)
    print(INSTALL_HINT if pystray is None else "no DISPLAY/WAYLAND_DISPLAY — CLI control loop", flush=True)
    print(
        "commands: status | apikey <key> | clear-key | link [project] | create | start | stop | pause | resume | sync | dashboard | config | quit",
        flush=True,
    )
    if core.signed_in() and not st_gui.validate(core.s):
        ok, msg = core.spawn()
        print(msg, flush=True)
    stop = {"v": False}

    def _sig(*_):
        stop["v"] = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        try: signal.signal(sig, _sig)
        except Exception: pass

    def poll():
        while not stop["v"] and not core._stop_flag:
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
        line = (line or "").strip()
        low = line.lower()
        if not low:
            return False
        if low in ("q", "quit", "exit"):
            return True
        if low == "status":
            core.fetch_hours(); print(core.status_line(), flush=True)
        elif low.startswith("apikey ") or low.startswith("api-key ") or low.startswith("key "):
            ok, msg = core.set_api_key(line.split(None, 1)[1])
            print(msg, flush=True)
        elif low in ("clear-key", "logout", "clear"):
            print(core.clear_api_key(), flush=True)
        elif low == "create":
            ok, msg = core.link_project_interactive(prefer_create=True)
            print(msg, flush=True)
        elif low == "link" or low.startswith("link "):
            parts = line.split(None, 1)
            if len(parts) == 1 and sys.stdin.isatty():
                ok, msg = core.link_project_interactive(prefer_create=False)
            else:
                ok, msg = core.link_project(parts[1] if len(parts) > 1 else None)
            print(msg, flush=True)
        elif low == "start":
            print(core.spawn()[1], flush=True)
        elif low == "stop":
            core.stop_daemon(); print("stopped", flush=True)
        elif low == "pause":
            core.pause(); print("paused", flush=True)
        elif low == "resume":
            core.resume(); print("resumed", flush=True)
        elif low in ("sync", "flush"):
            print(core.flush()[1], flush=True)
        elif low == "dashboard":
            try:
                core.open_dashboard(); print("opened dashboard", flush=True)
            except Exception as e:
                print(f"could not open browser: {e}", flush=True)
        elif low == "config":
            print(core.open_config(), flush=True)
        elif low == "login":
            print("browser login removed — use: apikey <StruggleTime API key from Settings>", flush=True)
        else:
            print("unknown command", flush=True)
        return False

    if not sys.stdin.isatty():
        had = False
        for line in sys.stdin:
            had = True
            if handle(line):
                break
        if not had:
            while not stop["v"]:
                time.sleep(1)
        core._stop_flag = True
        core.stop_daemon()
        return 0
    while not stop["v"]:
        try:
            line = input("tray> ")
        except EOFError:
            break
        if handle(line):
            break
    core._stop_flag = True
    core.stop_daemon()
    return 0

def main():
    import argparse
    ap = argparse.ArgumentParser(prog="struggletime-tray")
    ap.add_argument("--repo", default="")
    ap.add_argument("--user", default="")
    ap.add_argument("--server", default="")
    ap.add_argument("--token", default="", help="optional API key / session bearer")
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

    try:
        Tray(overrides).run()
    except Exception as e:
        print(f"tray failed to start: {e}", flush=True)
        print(INSTALL_HINT, flush=True)
        return run_headless(overrides)
    return 0

if __name__ == "__main__":
    sys.exit(main())
