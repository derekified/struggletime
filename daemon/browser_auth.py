#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Open the system default browser and complete desktop Slack login without pasting tokens."""
import json, os, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

DONE_HTML = """<!doctype html><meta charset=utf-8>
<title>StruggleTime</title>
<body style="font-family:system-ui;background:#0d0c0a;color:#f2efe9;display:grid;place-items:center;min-height:100vh;margin:0">
<div style="text-align:center;max-width:28rem;padding:2rem">
<h1 style="font-size:1.25rem;margin:0 0 .5rem">Signed in</h1>
<p style="color:#a39e93;margin:0">You can close this tab and return to StruggleTime.</p>
</div></body>
"""

ERR_HTML = """<!doctype html><meta charset=utf-8>
<title>StruggleTime</title>
<body style="font-family:system-ui;background:#0d0c0a;color:#f2efe9;display:grid;place-items:center;min-height:100vh;margin:0">
<div style="text-align:center;max-width:28rem;padding:2rem">
<h1 style="font-size:1.25rem;margin:0 0 .5rem">Sign-in failed</h1>
<p style="color:#a39e93;margin:0">{msg}</p>
</div></body>
"""


def open_default_browser(url):
    """Launch the OS default browser. Avoid webbrowser's wrong-browser / silent fails in tray processes."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must be http(s)")
    if sys.platform == "darwin":
        subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    if sys.platform.startswith("win"):
        try:
            os.startfile(url)  # noqa: PTH208 — intentional shell association
            return
        except OSError:
            subprocess.Popen(
                ["cmd", "/c", "start", "", url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
    env = {k: v for k, v in os.environ.items() if k not in ("LD_LIBRARY_PATH", "PYTHONHOME")}
    try:
        subprocess.Popen(
            ["xdg-open", url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
            start_new_session=True,
        )
        return
    except FileNotFoundError:
        pass
    import webbrowser
    webbrowser.open(url)


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


def exchange_auth_code(server, code):
    """Turn a one-time ?auth= code into a session bearer (no manual paste UI)."""
    code = (code or "").strip()
    if not code:
        return False, None, "missing auth code"
    status, data = api_json(server, "POST", "/v1/auth/session", body={"code": code})
    if status == 200 and data.get("token"):
        return True, data, None
    return False, None, (data.get("error") if isinstance(data, dict) else None) or f"session exchange failed ({status})"


def valid_desktop_redirect(url):
    try:
        u = urlparse(url)
    except Exception:
        return False
    if u.scheme != "http" or u.username or u.password:
        return False
    if (u.hostname or "").lower() not in ("127.0.0.1", "localhost"):
        return False
    if (u.path or "/") not in ("/", "/callback"):
        return False
    return True


class _CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path not in ("/", "/callback"):
            self.send_response(404); self.end_headers(); return
        qs = parse_qs(parsed.query)
        code = (qs.get("auth") or [""])[0].strip()
        err = (qs.get("oauth") or [""])[0].strip()
        owner = self.server.owner
        if code:
            owner.auth_code = code
            body = DONE_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            owner.done.set()
            return
        msg = err or "no auth code returned"
        owner.error = msg
        body = ERR_HTML.format(msg=msg).encode()
        self.send_response(400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        owner.done.set()


class DesktopLogin:
    """Local 127.0.0.1 callback + Slack OAuth via server desktop_redirect."""

    def __init__(self, server, public_url=""):
        self.server = server.rstrip("/")
        self.public = (public_url or server).rstrip("/")
        self.auth_code = None
        self.error = None
        self.done = threading.Event()
        self._httpd = None
        self._thread = None

    def _start_listener(self):
        self._httpd = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
        self._httpd.owner = self
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        port = self._httpd.server_address[1]
        return f"http://127.0.0.1:{port}/callback"

    def _stop_listener(self):
        if self._httpd:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            try:
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None

    def run(self, timeout=300, open_browser=True):
        """Open Slack sign-in and wait for the local callback. Returns (ok, session_dict|None, message)."""
        callback = self._start_listener()
        try:
            q = urllib.parse.urlencode({"desktop_redirect": callback})
            start = f"{self.public}/v1/auth/slack/start?{q}"
            if open_browser:
                open_default_browser(start)
            if not self.done.wait(timeout):
                return False, None, "sign-in timed out — try again"
            if self.error and not self.auth_code:
                return False, None, f"sign-in failed: {self.error}"
            ok, data, err = exchange_auth_code(self.server, self.auth_code)
            if not ok:
                return False, None, err
            if data.get("needs_handle"):
                return True, data, "signed in — claim a handle on the dashboard"
            handle = data.get("handle") or "?"
            return True, data, f"signed in as @{handle}"
        finally:
            self._stop_listener()
