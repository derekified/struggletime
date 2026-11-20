#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""StruggleTime daemon GUI: pick a repo and handle, press start tracking.

  python3 daemon/gui.py
  python3 daemon/gui.py --repo ~/my-project --user you

Thin window over daemon.py. It spawns the same daemon the CLI helper
runs, shows its log live, and reads the repo ledger to show how many
sealed buckets are waiting to send. Sign-in is browser-based (session
saved automatically; no manual token entry).
"""
import argparse, json, os, queue, subprocess, sys, threading, time
from pathlib import Path

sys.path = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).parent.resolve()]

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext
except ImportError:
    tk = None

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import config as dcfg
import browser_auth
HOME = dcfg.HOME
SETTINGS = dcfg.GUI_SETTINGS
DEFAULTS = dict(dcfg.DEFAULTS)

def load_settings(path=SETTINGS):
    s = dcfg.load_settings(path)
    return {**DEFAULTS, **{k: s.get(k, DEFAULTS.get(k, "")) for k in DEFAULTS}, "token": s.get("token", "")}

def save_settings(s, path=SETTINGS):
    dcfg.save_settings(s, path)

def validate(s):
    errs = []
    if not s["handle"].strip(): errs.append("pick your handle first (claim it on the dashboard after signing in)")
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
        self.auth_status = tk.StringVar(value="signed out")
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
        self.signin_btn = tk.Button(auth, text="sign in with slack", command=self.sign_in, bg="#d7ff6a", fg="#111")
        self.signin_btn.pack(side="right", padx=4)
        self.signout_btn = tk.Button(auth, text="sign out", command=self.sign_out)
        self.signout_btn.pack(side="right")
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
        self.log.insert("end", "sign in with slack (browser), then press start tracking.\n")
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
        if not self.token:
            self.auth_status.set("signed out")
            self.signin_btn.config(state="normal")
            self.signout_btn.config(state="disabled")
            return
        server = self.vars["server"].get().strip() or DEFAULTS["server"]
        code, data = browser_auth.api_json(server, "GET", "/v1/auth/me", token=self.token)
        if code == 200 and data.get("handle"):
            self.auth_status.set(f"signed in as @{data['handle']}")
            self.vars["handle"].set(data["handle"])
            self.signin_btn.config(state="disabled")
            self.signout_btn.config(state="normal")
        elif code == 200 and data.get("needs_handle"):
            self.auth_status.set("signed in — claim a handle on the dashboard")
            self.signin_btn.config(state="disabled")
            self.signout_btn.config(state="normal")
        else:
            self.auth_status.set("session expired — sign in again")
            self.token = ""
            self.signin_btn.config(state="normal")
            self.signout_btn.config(state="disabled")

    def sign_in(self):
        s = self._settings()
        server = s["server"] or DEFAULTS["server"]
        self.signin_btn.config(state="disabled")
        self.auth_status.set("opening browser…")
        self.log.insert("end", "opening Slack sign-in in your default browser…\n"); self.log.see("end")

        def work():
            try:
                login = browser_auth.DesktopLogin(server, public_base(server))
                ok, data, msg = login.run(timeout=300)
            except Exception as e:
                ok, data, msg = False, None, str(e)
            self.root.after(0, lambda: self._sign_in_done(ok, data, msg))

        threading.Thread(target=work, daemon=True).start()

    def _sign_in_done(self, ok, data, msg):
        self.log.insert("end", msg + "\n"); self.log.see("end")
        if ok and data and data.get("token"):
            self.token = data["token"]
            if data.get("handle"):
                self.vars["handle"].set(data["handle"])
            save_settings(self._settings())
            if data.get("needs_handle"):
                try:
                    browser_auth.open_default_browser(public_base(self.vars["server"].get()) + "/login")
                except Exception:
                    pass
        self._refresh_auth_label()
        if not ok:
            self.signin_btn.config(state="normal")
            messagebox.showerror("struggletime", msg)

    def sign_out(self):
        if self.token:
            browser_auth.api_json(self.vars["server"].get().strip(), "POST", "/v1/auth/logout", token=self.token)
        self.token = ""
        s = self._settings()
        s["token"] = ""
        save_settings(s)
        self._refresh_auth_label()
        self.log.insert("end", "signed out\n"); self.log.see("end")

    def open_dashboard(self):
        base = public_base(self.vars["server"].get().strip())
        path = "/home" if self.token and self.vars["handle"].get().strip() else "/login"
        try:
            browser_auth.open_default_browser(base + path)
        except Exception as e:
            messagebox.showerror("struggletime", f"could not open browser: {e}")

    def start(self):
        s = self._settings(); errs = validate(s)
        if errs: messagebox.showerror("struggletime", "\n".join(errs)); return
        if not self.token:
            messagebox.showerror("struggletime", "sign in with slack first"); return
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
