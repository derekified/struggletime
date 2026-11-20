#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""StruggleTime daemon GUI: pick a repo and handle, press start tracking.

  python3 daemon/gui.py
  python3 daemon/gui.py --repo ~/my-project --user you

Thin window over daemon.py. Links via StruggleTime API key (from
Settings on the dashboard), stored in ~/.aitime/gui.json.
"""
import argparse, json, os, queue, subprocess, sys, threading, time
from pathlib import Path

sys.path = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).parent.resolve()]

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, simpledialog
except ImportError:
    tk = None

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import config as dcfg
import browser_auth
def _settings_path():
    return dcfg.GUI_SETTINGS

HOME = dcfg.HOME  # legacy alias; prefer dcfg.HOME (may change in tests)
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

class App:
    def __init__(self, root, initial):
        self.root, self.proc, self.logq = root, None, queue.Queue()
        self.token = initial.get("token") or ""
        root.title("StruggleTime"); root.configure(bg="#0d0c0a"); root.minsize(560, 560)
        self.vars = {k: tk.StringVar(value=str(initial[k])) for k in DEFAULTS}
        self.status = tk.StringVar(value="not tracking")
        self.pending = tk.StringVar(value="")
        self.auth_status = tk.StringVar(value="no API key")
        pad = {"padx": 14, "pady": 4}
        tk.Label(root, text="struggletime", font=("TkDefaultFont", 17, "bold"), bg="#0d0c0a", fg="#f2efe9").pack(anchor="w", **pad)
        tk.Label(root, text="tracks your struggle time while you code. metadata only, no screenshots, no keystrokes.",
                 bg="#0d0c0a", fg="#a39e93").pack(anchor="w", padx=14)
        form = tk.Frame(root, bg="#0d0c0a"); form.pack(fill="x", pady=8)
        self._row(form, 0, "your handle", "handle")
        self._row(form, 1, "repo to track", "repo", browse=True)
        self._row(form, 2, "server", "server")
        self._row(form, 3, "bucket (minutes)", "bucket_mins")
        auth = tk.Frame(root, bg="#0d0c0a"); auth.pack(fill="x", **pad)
        tk.Label(auth, textvariable=self.auth_status, bg="#0d0c0a", fg="#a39e93").pack(side="left")
        self.link_btn = tk.Button(auth, text="set API key…", command=self.set_api_key, bg="#d7ff6a", fg="#111")
        self.link_btn.pack(side="right", padx=4)
        self.clear_btn = tk.Button(auth, text="clear API key", command=self.clear_api_key)
        self.clear_btn.pack(side="right")
        btns = tk.Frame(root, bg="#0d0c0a"); btns.pack(fill="x", **pad)
        self.start_btn = tk.Button(btns, text="start tracking", command=self.start, bg="#d7ff6a", fg="#111", font=("TkDefaultFont", 11, "bold"))
        self.start_btn.pack(side="left")
        self.stop_btn = tk.Button(btns, text="stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        tk.Button(btns, text="open dashboard", command=self.open_dashboard).pack(side="left")
        tk.Button(btns, text="inspect what would be sent", command=self.inspect).pack(side="left", padx=8)
        tk.Label(root, textvariable=self.status, bg="#0d0c0a", fg="#d7ff6a", font=("TkDefaultFont", 11, "bold")).pack(anchor="w", **pad)
        tk.Label(root, textvariable=self.pending, bg="#0d0c0a", fg="#a39e93").pack(anchor="w", padx=14)
        self.log = scrolledtext.ScrolledText(root, height=16, bg="#191817", fg="#f2efe9", insertbackground="#f2efe9", font=("TkFixedFont", 9))
        self.log.pack(fill="both", expand=True, padx=14, pady=10)
        self.log.insert("end", "generate an API key in Settings on the dashboard, then set it here.\n")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._refresh_auth_label()
        self.root.after(300, self._tick)

    def _row(self, form, r, label, key, browse=False):
        tk.Label(form, text=label, width=16, anchor="w", bg="#0d0c0a", fg="#a39e93").grid(row=r, column=0, padx=14, pady=4, sticky="w")
        tk.Entry(form, textvariable=self.vars[key], width=44, bg="#211f1d", fg="#f2efe9", insertbackground="#f2efe9").grid(row=r, column=1, pady=4, sticky="we")
        if browse: tk.Button(form, text="browse…", command=self._browse).grid(row=r, column=2, padx=6)
        form.columnconfigure(1, weight=1)

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.vars["repo"].get() or str(Path.home()))
        if d: self.vars["repo"].set(d)

    def _settings(self):
        s = {k: v.get().strip() for k, v in self.vars.items()}
        try: s["bucket_mins"] = int(s["bucket_mins"])
        except Exception: s["bucket_mins"] = 5
        if self.token:
            s["token"] = self.token
        return s

    def _refresh_auth_label(self):
        # pick up key saved by the tray
        disk = load_settings()
        if disk.get("token") and disk.get("token") != self.token:
            self.token = disk["token"]
        if not self.token:
            self.auth_status.set("no API key")
            self.link_btn.config(state="normal")
            self.clear_btn.config(state="disabled")
            return
        server = self.vars["server"].get().strip() or DEFAULTS["server"]
        code, data = browser_auth.api_json(server, "GET", "/v1/auth/me", token=self.token)
        if code == 200 and data.get("handle"):
            self.auth_status.set(f"API key linked · @{data['handle']}")
            self.vars["handle"].set(data["handle"])
            self.link_btn.config(state="normal")
            self.clear_btn.config(state="normal")
        else:
            self.auth_status.set("API key invalid — set a new one")
            self.link_btn.config(state="normal")
            self.clear_btn.config(state="normal")

    def set_api_key(self):
        val = simpledialog.askstring(
            "StruggleTime API key",
            "Paste the key from Settings → StruggleTime API key:",
            parent=self.root, show="*",
        )
        if val is None:
            return
        key = val.strip()
        if not key:
            messagebox.showerror("struggletime", "no key entered"); return
        server = self.vars["server"].get().strip() or DEFAULTS["server"]
        self.auth_status.set("validating…")
        self.root.update_idletasks()
        code, data = browser_auth.api_json(server, "GET", "/v1/auth/me", token=key)
        if code != 200 or not data.get("handle"):
            err = (data.get("error") if isinstance(data, dict) else None) or f"rejected ({code})"
            self._refresh_auth_label()
            messagebox.showerror("struggletime", f"API key invalid: {err}")
            return
        self.token = key
        self.vars["handle"].set(data["handle"])
        save_settings(self._settings())
        self._refresh_auth_label()
        self.log.insert("end", f"API key linked as @{data['handle']}\n"); self.log.see("end")

    def clear_api_key(self):
        self.token = ""
        s = self._settings()
        s["token"] = ""
        save_settings(s)
        self._refresh_auth_label()
        self.log.insert("end", "API key cleared\n"); self.log.see("end")

    def open_dashboard(self):
        base = public_base(self.vars["server"].get().strip())
        path = "/settings" if self.token else "/login"
        try:
            browser_auth.open_default_browser(base + path)
        except Exception as e:
            messagebox.showerror("struggletime", f"could not open browser: {e}")

    def start(self):
        s = self._settings(); errs = validate(s)
        if errs: messagebox.showerror("struggletime", "\n".join(errs)); return
        if not self.token:
            messagebox.showerror("struggletime", "set your StruggleTime API key first"); return
        save_settings(s)
        kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True, "bufsize": 1}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(daemon_cmd(s), **kwargs)
        threading.Thread(target=self._pump, daemon=True).start()
        self.status.set(f"tracking {s['repo']} as @{s['handle']}")
        self.start_btn.config(state="disabled"); self.stop_btn.config(state="normal")
        self.log.insert("end", f"started (pid {self.proc.pid})\n"); self.log.see("end")

    def _pump(self):
        for line in self.proc.stdout: self.logq.put(line)

    def stop(self):
        if self.proc and self.proc.poll() is None: self.proc.terminate()
        self.proc = None; self.status.set("not tracking")
        self.start_btn.config(state="normal"); self.stop_btn.config(state="disabled")

    def close(self):
        self.stop(); self.root.destroy()

    def inspect(self):
        repo = self.vars["repo"].get().strip()
        if not repo: messagebox.showerror("struggletime", "pick a repo first"); return
        r = subprocess.run([sys.executable, str(ROOT / "daemon" / "tracker_inspect.py"), repo], capture_output=True, text=True)
        self.log.insert("end", "\n--- inspect ---\n" + (r.stdout or r.stderr) + "\n"); self.log.see("end")

    def _tick(self):
        while not self.logq.empty(): self.log.insert("end", self.logq.get()); self.log.see("end")
        if self.proc and self.proc.poll() is not None:
            self.log.insert("end", f"daemon exited ({self.proc.returncode}). if the server is down, buckets wait in the repo ledger.\n")
            self.stop()
        repo = Path(self.vars["repo"].get().strip()).expanduser()
        ledger = repo / ".aitime-ledger.json"
        if ledger.exists():
            try: self.pending.set(f"{len(json.loads(ledger.read_text()).get('pending', []))} sealed bucket(s) waiting to send · ledger {ledger}")
            except Exception: pass
        # keep auth label in sync with tray writes to gui.json
        if int(time.time()) % 4 == 0:
            self._refresh_auth_label()
        self.root.after(2000, self._tick)

def main():
    ap = argparse.ArgumentParser(prog="struggletime")
    ap.add_argument("--repo", default=""); ap.add_argument("--user", default=""); ap.add_argument("--server", default="")
    ap.add_argument("--check", action="store_true", help="verify the gui can start on this machine, then exit")
    a = ap.parse_args()
    if tk is None: print("tkinter is missing. on debian/ubuntu: sudo apt install python3-tk · on mac/windows it ships with python.org builds"); return 1
    if a.check:
        r = tk.Tk(); r.withdraw(); r.destroy(); print("gui ok: tkinter available"); return 0
    s = load_settings()
    if a.repo: s["repo"] = a.repo
    if a.user: s["handle"] = a.user
    if a.server: s["server"] = a.server
    root = tk.Tk(); App(root, s); root.mainloop(); return 0

if __name__ == "__main__":
    sys.exit(main())
