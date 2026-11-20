#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared aitime daemon config: ~/.aitime/gui.json only (no wakatime.cfg)."""
import json, os
from pathlib import Path

HOME = Path(os.environ.get("STRUGGLETIME_HOME") or os.environ.get("AITIME_HOME") or (Path.home() / ".aitime"))
GUI_SETTINGS = HOME / "gui.json"
PAUSE_FLAG = HOME / "paused"

DEFAULTS = {
    "handle": "",
    "repo": "",
    "server": "http://127.0.0.1:18765",
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

def load_settings(path=None):
    p = Path(path or GUI_SETTINGS)
    data = dict(DEFAULTS)
    try:
        raw = json.loads(p.read_text())
        data.update({k: raw[k] for k in raw if k in DEFAULTS or k == "token"})
    except Exception:
        pass
    data["server"] = env("STRUGGLETIME_SERVER", "AITIME_SERVER", default=data.get("server") or DEFAULTS["server"])
    data["token"] = env("STRUGGLETIME_TOKEN", "AITIME_TOKEN", default=data.get("token") or "")
    data["handle"] = env("STRUGGLETIME_USER", "AITIME_USER", default=data.get("handle") or "")
    data["repo"] = env("STRUGGLETIME_REPO", "AITIME_REPO", default=data.get("repo") or "")
    return data

def save_settings(s, path=None):
    p = Path(path or GUI_SETTINGS)
    p.parent.mkdir(parents=True, exist_ok=True)
    keep = {k: s.get(k, DEFAULTS.get(k, "")) for k in DEFAULTS}
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
    server = (cli_server or s.get("server") or DEFAULTS["server"]).rstrip("/")
    token = cli_token or s.get("token") or ""
    user = cli_user or s.get("handle") or ""
    repo = cli_repo or s.get("repo") or ""
    project = cli_project or s.get("project") or ""
    return {
        "server": server,
        "token": token,
        "handle": user,
        "repo": repo,
        "project": project,
        "bucket_mins": int(s.get("bucket_mins") or 5),
    }
