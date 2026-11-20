#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared aitime daemon config: ~/.aitime/gui.json only (no wakatime.cfg)."""
import json, os
from pathlib import Path
from urllib.parse import urlparse

def _load_dotenv():
    here = Path(__file__).resolve().parent.parent
    for candidate in (Path.cwd() / ".env", here / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))
        break

_load_dotenv()

HOME = Path(os.environ.get("STRUGGLETIME_HOME") or os.environ.get("AITIME_HOME") or (Path.home() / ".aitime"))
GUI_SETTINGS = HOME / "gui.json"
PAUSE_FLAG = HOME / "paused"

# Public Cloudflare tunnel (replaces stale <NGROK …> / localhost defaults).
PUBLIC_SERVER = "https://paper-identifier-specialist-sold.trycloudflare.com"

DEFAULTS = {
    "handle": "",
    "repo": "",
    "server": PUBLIC_SERVER,
    "token": "",
    "bucket_mins": 5,
    "project": "",
}

def env(*keys, default=""):
    for k in keys:
        v = os.environ.get(k)
        if v:
            return v
    return default

def normalize_server(url, fallback=None):
    """Return a usable http(s) base URL; replace placeholders / junk with PUBLIC_SERVER."""
    fallback = (fallback or PUBLIC_SERVER).rstrip("/")
    raw = (url or "").strip()
    if not raw:
        return fallback
    low = raw.lower()
    # Explicit stale placeholders (e.g. "<NGROK, nothing after it>")
    if raw.startswith("<") or "ngrok" in low and not low.startswith("http"):
        return fallback
    if "<ngrok" in low or "nothing after" in low:
        return fallback
    if not raw.startswith(("http://", "https://")):
        return fallback
    try:
        u = urlparse(raw)
    except Exception:
        return fallback
    if u.scheme not in ("http", "https") or not u.netloc:
        return fallback
    # reject scheme-only / malformed hosts
    if u.netloc.startswith("<") or " " in u.netloc:
        return fallback
    return raw.rstrip("/")

def public_url(fallback=""):
    """Browser-facing base (OAuth / dashboard). Prefer tunnel/public host over localhost."""
    return normalize_server(
        env("OAUTH_REDIRECT_BASE", "STRUGGLETIME_PUBLIC_URL", "AITIME_PUBLIC_URL", default="")
        or fallback
        or env("STRUGGLETIME_SERVER", "AITIME_SERVER", default=DEFAULTS["server"])
    )

def load_settings(path=None):
    p = Path(path or GUI_SETTINGS)
    data = dict(DEFAULTS)
    raw_server = None
    try:
        raw = json.loads(p.read_text())
        data.update({k: raw[k] for k in raw if k in DEFAULTS or k == "token"})
        raw_server = raw.get("server")
    except Exception:
        pass
    data["server"] = env("STRUGGLETIME_SERVER", "AITIME_SERVER", default=data.get("server") or DEFAULTS["server"])
    # if settings still point at loopback but a public tunnel is configured, use that for the app URL
    pub = env("OAUTH_REDIRECT_BASE", "STRUGGLETIME_PUBLIC_URL", "AITIME_PUBLIC_URL", default="")
    if pub and ("127.0.0.1" in data["server"] or "localhost" in data["server"]):
        data["server"] = pub.rstrip("/")
    data["server"] = normalize_server(data["server"])
    data["token"] = env("STRUGGLETIME_TOKEN", "AITIME_TOKEN", default=data.get("token") or "")
    data["handle"] = env("STRUGGLETIME_USER", "AITIME_USER", default=data.get("handle") or "")
    data["repo"] = env("STRUGGLETIME_REPO", "AITIME_REPO", default=data.get("repo") or "")
    # Persist when on-disk server was a placeholder / invalid / superseded by public URL
    if p.exists() and raw_server is not None:
        if data["server"] != (raw_server or "").strip().rstrip("/"):
            try:
                save_settings(data, p)
            except Exception:
                pass
    return data

def save_settings(s, path=None):
    p = Path(path or GUI_SETTINGS)
    p.parent.mkdir(parents=True, exist_ok=True)
    keep = {k: s.get(k, DEFAULTS.get(k, "")) for k in DEFAULTS}
    keep["server"] = normalize_server(keep.get("server"))
    if s.get("token"):
        keep["token"] = s["token"]
    p.write_text(json.dumps(keep, indent=1))

def is_paused(path=None):
    return Path(path or PAUSE_FLAG).exists()

def set_paused(paused, path=None):
    p = Path(path or PAUSE_FLAG)
    p.parent.mkdir(parents=True, exist_ok=True)
    if paused:
        p.write_text("1\n")
    else:
        p.unlink(missing_ok=True)

def resolve_runtime(cli_server="", cli_token="", cli_user="", cli_repo="", cli_project=""):
    """Merge CLI > env > ~/.aitime/gui.json."""
    s = load_settings()
    server = normalize_server(cli_server or s.get("server") or DEFAULTS["server"])
    token = cli_token or s.get("token") or ""
    user = cli_user or s.get("handle") or ""
    repo = cli_repo or s.get("repo") or ""
    project = cli_project or s.get("project") or ""
    return {
        "server": server,
        "public_url": public_url(server),
        "token": token,
        "handle": user,
        "repo": repo,
        "project": project,
        "bucket_mins": int(s.get("bucket_mins") or 5),
    }
