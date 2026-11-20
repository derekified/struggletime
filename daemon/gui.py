#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""StruggleTime daemon settings — no tkinter.

  python3 daemon/gui.py
  python3 daemon/gui.py --repo ~/my-project --user you

Uses native OS dialogs (osascript / zenity / PowerShell) when a display is
available, otherwise a simple terminal menu. Shared state: ~/.aitime/gui.json.
"""
import argparse, json, os, subprocess, sys, threading, time
from pathlib import Path

sys.path = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).parent.resolve()]

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import config as dcfg
import browser_auth
import native_ui
import project_link

# Kept for tray/tests that historically checked gui.tk — always None (no tkinter).
tk = None

HOME = dcfg.HOME
SETTINGS = dcfg.GUI_SETTINGS
DEFAULTS = dict(dcfg.DEFAULTS)

def load_settings(path=None):
    s = dcfg.load_settings(path or dcfg.GUI_SETTINGS)
    return {**DEFAULTS, **{k: s.get(k, DEFAULTS.get(k, "")) for k in DEFAULTS}, "token": s.get("token", "")}

def save_settings(s, path=None):
    dcfg.save_settings(s, path or dcfg.GUI_SETTINGS)

def validate(s):
    errs = []
    if not s["handle"].strip(): errs.append("set your API key (handle is filled from the key)")
    repo = Path(s["repo"]).expanduser()
    if not s["repo"].strip() or not repo.is_dir(): errs.append("pick a repo folder that exists")
    if not s["server"].startswith(("http://", "https://")): errs.append("server should start with http:// or https://")
    return errs

def daemon_cmd(s):
    cmd = [sys.executable, str(ROOT / "daemon" / "daemon.py"), "--repo", str(Path(s["repo"]).expanduser()),
           "--server", s["server"].rstrip("/"), "--user", s["handle"].strip(),
           "--bucket-secs", str(max(1, int(s.get("bucket_mins") or 5)) * 60)]
    if s.get("project"): cmd += ["--project", str(s["project"])]
    if s.get("token"): cmd += ["--token", str(s["token"])]
    return cmd

def public_base(server):
    return dcfg.public_url(server or "")

def _auth_label(s):
    token = s.get("token") or ""
    if not token:
        return "no API key"
    code, data = browser_auth.api_json(s["server"], "GET", "/v1/auth/me", token=token)
    if code == 200 and data.get("handle"):
        s["handle"] = data["handle"]
        return f"API key linked · @{data['handle']}"
    return "API key invalid — set a new one"

def set_api_key_interactive(s):
    val = native_ui.prompt(
        "StruggleTime API key",
        "Paste the key from Settings → StruggleTime API key:",
        secret=True,
    )
    if val is None:
        return s, "cancelled"
    key = val.strip()
    if not key:
        native_ui.alert("StruggleTime", "No key entered.")
        return s, "no key"
    if key.startswith("<") or "ngrok" in key.lower():
        native_ui.alert("StruggleTime", "That looks like a placeholder. Paste the real st_… key from Settings.")
        return s, "placeholder"
    s["server"] = dcfg.normalize_server(s.get("server"))
    code, data = browser_auth.api_json(s["server"], "GET", "/v1/auth/me", token=key)
    if code != 200 or not data.get("handle"):
        err = (data.get("error") if isinstance(data, dict) else None) or f"rejected ({code})"
        native_ui.alert("StruggleTime", f"API key invalid: {err}")
        return s, "invalid"
    s["token"] = key
    s["handle"] = data["handle"]
    save_settings(s)
    native_ui.alert("StruggleTime", f"Linked as @{data['handle']}")
    return s, f"linked as @{data['handle']}"

def clear_api_key_interactive(s):
    if not native_ui.confirm("StruggleTime", "Clear the API key from this machine?"):
        return s, "cancelled"
    s["token"] = ""
    save_settings(s)
    return s, "cleared"

def pick_repo(s):
    path = native_ui.choose_folder("Local directory to track", s.get("repo") or str(Path.home()))
    if not path:
        return s, "cancelled"
    s["repo"] = path
    save_settings(s)
    return s, path

def link_or_create_project(s, *, prefer_create=False):
    s, msg = project_link.interactive_link(s, prefer_create=prefer_create)
    save_settings(s)
    return s, msg

def edit_field(s, key, title, secret=False):
    cur = str(s.get(key) or "")
    val = native_ui.prompt(title, f"Current: {cur or '(empty)'}\nNew value:", secret=secret, default=cur)
    if val is None:
        return s, "cancelled"
    s[key] = val.strip()
    if key == "bucket_mins":
        try:
            s["bucket_mins"] = int(s["bucket_mins"])
        except Exception:
            s["bucket_mins"] = 5
    save_settings(s)
    return s, s[key]

class Tracker:
    def __init__(self):
        self.proc = None

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, s):
        errs = validate(s)
        if errs:
            native_ui.alert("StruggleTime", "\n".join(errs))
            return False, "; ".join(errs)
        if not s.get("token"):
            native_ui.alert("StruggleTime", "Set your StruggleTime API key first.")
            return False, "no API key"
        save_settings(s)
        kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True, "bufsize": 1}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(daemon_cmd(s), **kwargs)
        threading.Thread(target=self._drain, daemon=True).start()
        return True, f"tracking {s['repo']} as @{s['handle']} (pid {self.proc.pid})"

    def _drain(self):
        try:
            for line in self.proc.stdout:
                print(line, end="", flush=True)
        except Exception:
            pass

    def stop(self):
        if self.running():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
        self.proc = None
        return "stopped"

def inspect_repo(s):
    repo = (s.get("repo") or "").strip()
    if not repo or not Path(repo).expanduser().is_dir():
        native_ui.alert("StruggleTime", "Pick a repo first.")
        return
    r = subprocess.run(
        [sys.executable, str(ROOT / "daemon" / "tracker_inspect.py"), str(Path(repo).expanduser())],
        capture_output=True, text=True,
    )
    native_ui.alert("Inspect — what would be sent", (r.stdout or r.stderr or "nothing").strip())

def open_dashboard(s):
    base = public_base(s.get("server") or "")
    path = "/settings" if s.get("token") else "/login"
    try:
        browser_auth.open_default_browser(base + path)
    except Exception as e:
        native_ui.alert("StruggleTime", f"Could not open browser: {e}")

def print_status(s, tracker):
    auth = _auth_label(s)
    state = "tracking" if tracker.running() else "idle"
    print(f"\nstruggletime settings  [{native_ui.backend_name()} dialogs]")
    print(f"  status:  {state} · {auth}")
    print(f"  handle:  {s.get('handle') or '—'}")
    print(f"  project: {s.get('project') or '—'}")
    print(f"  dir:     {s.get('repo') or '—'}")
    print(f"  server:  {s.get('server')}")
    print(f"  bucket:  {s.get('bucket_mins')} min")
    print(f"  config:  {dcfg.GUI_SETTINGS}")
    ledger = Path(s.get("repo") or ".").expanduser() / ".aitime-ledger.json"
    if ledger.exists():
        try:
            n = len(json.loads(ledger.read_text()).get("pending", []))
            print(f"  pending: {n} sealed bucket(s)")
        except Exception:
            pass

def run_menu(initial):
    s = dict(initial)
    tracker = Tracker()
    actions = [
        ("1", "set / link API key", lambda: set_api_key_interactive(s)),
        ("2", "clear API key", lambda: clear_api_key_interactive(s)),
        ("3", "link / create project", lambda: link_or_create_project(s)),
        ("4", "create project", lambda: link_or_create_project(s, prefer_create=True)),
        ("5", "choose directory only", lambda: pick_repo(s)),
        ("6", "edit server URL", lambda: edit_field(s, "server", "Server URL")),
        ("7", "edit bucket minutes", lambda: edit_field(s, "bucket_mins", "Bucket minutes")),
        ("8", "start tracking", None),
        ("9", "stop tracking", None),
        ("i", "inspect what would be sent", lambda: (inspect_repo(s), s, "ok")[1:]),
        ("d", "open dashboard", lambda: (open_dashboard(s), s, "ok")[1:]),
        ("s", "show status", None),
        ("q", "quit", None),
    ]

    # Non-interactive / one-shot: if display, offer API key then exit-friendly menu
    while True:
        print_status(s, tracker)
        print()
        for key, label, _ in actions:
            print(f"  {key}) {label}")
        try:
            choice = input("\nsettings> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if choice in ("q", "quit", "exit"):
            break
        if choice == "s" or choice == "":
            continue
        if choice == "8":
            ok, msg = tracker.start(s)
            print(msg)
            if not ok:
                native_ui.alert("StruggleTime", msg)
            continue
        if choice == "9":
            print(tracker.stop())
            continue
        if choice == "i":
            inspect_repo(s)
            continue
        if choice == "d":
            open_dashboard(s)
            continue
        matched = next((a for a in actions if a[0] == choice), None)
        if not matched or matched[2] is None:
            print("unknown command")
            continue
        s, msg = matched[2]()
        if msg:
            print(msg)
    tracker.stop()
    save_settings(s)
    return 0

def run_wizard(initial):
    """Display-friendly flow: native prompts for the common setup path."""
    s = dict(initial)
    print(f"struggletime settings ({native_ui.backend_name()})", flush=True)
    if not s.get("token"):
        s, msg = set_api_key_interactive(s)
        print(msg, flush=True)
    if not s.get("repo") or not Path(s.get("repo") or "").expanduser().is_dir():
        s, msg = pick_repo(s)
        print(msg, flush=True)
    print_status(s, Tracker())
    if native_ui.confirm("StruggleTime", "Open the interactive settings menu?"):
        return run_menu(s)
    save_settings(s)
    return 0

def main():
    ap = argparse.ArgumentParser(prog="struggletime")
    ap.add_argument("--repo", default="")
    ap.add_argument("--user", default="")
    ap.add_argument("--server", default="")
    ap.add_argument("--menu", action="store_true", help="force terminal menu")
    ap.add_argument("--check", action="store_true", help="verify settings UI can start, then exit")
    a = ap.parse_args()
    if a.check:
        print(f"gui ok: native_ui backend={native_ui.backend_name()} (no tkinter)")
        return 0
    s = load_settings()
    if a.repo: s["repo"] = a.repo
    if a.user: s["handle"] = a.user
    if a.server: s["server"] = a.server
    if a.menu or not native_ui.has_display() or not sys.stdin.isatty():
        if not sys.stdin.isatty():
            save_settings(s)
            print(f"settings written to {dcfg.GUI_SETTINGS}", flush=True)
            print("run interactively for the menu, or use the tray: python3 daemon/tray.py", flush=True)
            return 0
        return run_menu(s)
    return run_wizard(s)

if __name__ == "__main__":
    sys.exit(main())
