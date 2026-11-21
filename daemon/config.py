#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared StruggleTime/aitime config paths.

Settings (API key, server, linked repo) live under XDG config:
  ~/.config/struggletime/settings.json
Runtime data (logs, events, pause flag) stay under:
  ~/.aitime/

Legacy ~/.aitime/gui.json is migrated automatically on first load.
"""
import json, os, shutil
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

def _xdg_config_home():
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))

# Runtime / state dir (logs, agent_events, pause). Overridable for tests.
HOME = Path(os.environ.get("STRUGGLETIME_HOME") or os.environ.get("AITIME_HOME") or (Path.home() / ".aitime"))

# Persistent settings dir (XDG). STRUGGLETIME_CONFIG / AITIME_CONFIG override.
CONFIG_HOME = Path(
    os.environ.get("STRUGGLETIME_CONFIG")
    or os.environ.get("AITIME_CONFIG")
    or (_xdg_config_home() / "struggletime")
)

GUI_SETTINGS = CONFIG_HOME / "settings.json"
LEGACY_GUI_SETTINGS = HOME / "gui.json"
EDITORS_JSON = CONFIG_HOME / "editors.json"
LEGACY_EDITORS_JSON = HOME / "editors.json"
PAUSE_FLAG = HOME / "paused"
DAEMON_PID = HOME / "daemon.pid"
DAEMON_LOG = HOME / "daemon.log"
DAEMON_ARGS = HOME / "daemon.args"

# Public origin (IP + port-forward). Override via STRUGGLETIME_SERVER / OAUTH_REDIRECT_BASE.
PUBLIC_SERVER = "https://62-83-116-143.sslip.io"

DEFAULTS = {
    "handle": "",
    "repo": "",
    "server": PUBLIC_SERVER,
    "token": "",
    "bucket_mins": 5,
    "project": "",
    "projects": [],  # [{id, path}, ...] — all linked roots the daemon watches
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
    if raw.startswith("<") or "ngrok" in low and not low.startswith("http"):
        return fallback
    if "<ngrok" in low or "nothing after" in low:
        return fallback
    # Ephemeral tunnels die when the process exits — never keep them as the daemon target
    if "trycloudflare.com" in low or "cfargotunnel.com" in low or ".loca.lt" in low:
        return fallback
    if not raw.startswith(("http://", "https://")):
        return fallback
    try:
        u = urlparse(raw)
    except Exception:
        return fallback
    if u.scheme not in ("http", "https") or not u.netloc:
        return fallback
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

def _migrate_legacy_settings():
    """Copy ~/.aitime/gui.json → ~/.config/struggletime/settings.json once."""
    if GUI_SETTINGS.exists():
        return
    if not LEGACY_GUI_SETTINGS.exists():
        return
    try:
        CONFIG_HOME.mkdir(parents=True, exist_ok=True)
        shutil.copy2(LEGACY_GUI_SETTINGS, GUI_SETTINGS)
    except Exception:
        pass

def _migrate_legacy_editors():
    if EDITORS_JSON.exists() or not LEGACY_EDITORS_JSON.exists():
        return
    try:
        CONFIG_HOME.mkdir(parents=True, exist_ok=True)
        shutil.copy2(LEGACY_EDITORS_JSON, EDITORS_JSON)
    except Exception:
        pass

def settings_path(path=None):
    if path:
        return Path(path)
    _migrate_legacy_settings()
    return GUI_SETTINGS

def _normalize_projects(raw_projects, primary_repo="", primary_project=""):
    """Dedupe linked {id, path} rows; always include legacy primary repo/project."""
    out, seen = [], set()

    def add(pid, path):
        path = str(path or "").strip()
        if not path:
            return
        try:
            key = str(Path(path).expanduser().resolve())
        except Exception:
            key = str(Path(path).expanduser())
        if key in seen:
            # Prefer filling in a missing id on an existing row
            if pid:
                for row in out:
                    try:
                        same = str(Path(row["path"]).expanduser().resolve()) == key
                    except Exception:
                        same = row["path"] == path
                    if same and not row.get("id"):
                        row["id"] = str(pid)
            return
        seen.add(key)
        out.append({"id": str(pid or "").strip(), "path": path})

    if isinstance(raw_projects, list):
        for row in raw_projects:
            if isinstance(row, dict):
                add(row.get("id") or row.get("project"), row.get("path") or row.get("repo"))
            elif isinstance(row, str):
                add("", row)
    if primary_repo:
        add(primary_project, primary_repo)
    return out

def load_settings(path=None):
    p = settings_path(path)
    data = dict(DEFAULTS)
    data["projects"] = []
    raw_server = None
    try:
        raw = json.loads(p.read_text())
        data.update({k: raw[k] for k in raw if k in DEFAULTS or k == "token"})
        raw_server = raw.get("server")
        data["projects"] = _normalize_projects(
            raw.get("projects"), raw.get("repo") or "", raw.get("project") or "",
        )
    except Exception:
        pass
    data["server"] = env("STRUGGLETIME_SERVER", "AITIME_SERVER", default=data.get("server") or DEFAULTS["server"])
    pub = env("OAUTH_REDIRECT_BASE", "STRUGGLETIME_PUBLIC_URL", "AITIME_PUBLIC_URL", default="")
    if pub and ("127.0.0.1" in data["server"] or "localhost" in data["server"]):
        data["server"] = pub.rstrip("/")
    data["server"] = normalize_server(data["server"])
    data["token"] = env("STRUGGLETIME_TOKEN", "AITIME_TOKEN", default=data.get("token") or "")
    data["handle"] = env("STRUGGLETIME_USER", "AITIME_USER", default=data.get("handle") or "")
    data["repo"] = env("STRUGGLETIME_REPO", "AITIME_REPO", default=data.get("repo") or "")
    data["projects"] = _normalize_projects(data.get("projects"), data.get("repo") or "", data.get("project") or "")
    if p.exists() and raw_server is not None:
        if data["server"] != (raw_server or "").strip().rstrip("/"):
            try:
                save_settings(data, p)
            except Exception:
                pass
    return data

def save_settings(s, path=None):
    p = settings_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    HOME.mkdir(parents=True, exist_ok=True)
    keep = {k: s.get(k, DEFAULTS.get(k, "")) for k in DEFAULTS if k != "projects"}
    keep["server"] = normalize_server(keep.get("server"))
    if s.get("token"):
        keep["token"] = s["token"]
    keep["projects"] = _normalize_projects(
        s.get("projects"), keep.get("repo") or "", keep.get("project") or "",
    )
    p.write_text(json.dumps(keep, indent=1) + "\n")
    # Keep a compatibility copy for older tooling that still reads ~/.aitime/gui.json
    try:
        if p.resolve() != LEGACY_GUI_SETTINGS.resolve():
            LEGACY_GUI_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            LEGACY_GUI_SETTINGS.write_text(p.read_text())
    except Exception:
        pass

def linked_projects(settings=None):
    """Return [{id, path}, ...] for every directory the daemon should watch."""
    s = settings if settings is not None else load_settings()
    return _normalize_projects(s.get("projects"), s.get("repo") or "", s.get("project") or "")

def upsert_linked_project(settings, *, project_id, local_path):
    """Add/update a linked project root and set it as the primary repo/project."""
    s = dict(settings or {})
    path = str(Path(local_path).expanduser()) if local_path else ""
    pid = str(project_id or "").strip()
    rows = [r for r in (s.get("projects") or []) if isinstance(r, dict)]
    if path:
        try:
            key = str(Path(path).expanduser().resolve())
        except Exception:
            key = path
        updated = False
        for row in rows:
            try:
                same = str(Path(row.get("path") or "").expanduser().resolve()) == key
            except Exception:
                same = (row.get("path") or "") == path
            if same:
                row["path"] = path
                if pid:
                    row["id"] = pid
                updated = True
                break
        if not updated:
            rows.append({"id": pid, "path": path})
        s["repo"] = path
    if pid:
        s["project"] = pid
    s["projects"] = _normalize_projects(rows, s.get("repo") or "", s.get("project") or "")
    return s

def resolve_runtime(cli_server=None, cli_token=None, cli_user="", cli_repo="", cli_project=""):
    """Merge CLI > env > settings.json.

    cli_server/cli_token: None = fall back to settings; "" = explicit offline / no token.
    """
    s = load_settings()
    if cli_server is None:
        server = normalize_server(s.get("server") or DEFAULTS["server"])
    elif not str(cli_server).strip():
        server = ""  # intentional offline (do not coerce to PUBLIC_SERVER)
    else:
        server = normalize_server(cli_server)
    if cli_token is None:
        token = s.get("token") or ""
    else:
        token = str(cli_token)
    user = cli_user or s.get("handle") or ""
    repo = cli_repo or s.get("repo") or ""
    project = cli_project or s.get("project") or ""
    projects = linked_projects(s)
    if cli_repo:
        projects = _normalize_projects(
            projects + [{"id": cli_project or "", "path": cli_repo}],
            cli_repo, cli_project or "",
        )
    return {
        "server": server,
        "public_url": public_url(server) if server else "",
        "token": token,
        "handle": user,
        "repo": repo,
        "project": project,
        "projects": projects,
        "bucket_mins": int(s.get("bucket_mins") or 5),
    }

def is_paused(path=None):
    return Path(path or PAUSE_FLAG).exists()

def set_paused(paused, path=None):
    p = Path(path or PAUSE_FLAG)
    p.parent.mkdir(parents=True, exist_ok=True)
    if paused:
        p.write_text("1\n")
    else:
        p.unlink(missing_ok=True)

def editors_path():
    _migrate_legacy_editors()
    return Path(os.environ.get("AITIME_EDITORS_JSON") or EDITORS_JSON)

def daemon_alive(pid=None):
    """True if the tracker daemon pid is still running."""
    if pid is None:
        try:
            pid = int(DAEMON_PID.read_text().strip())
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False

def read_daemon_pid():
    try:
        pid = int(DAEMON_PID.read_text().strip())
    except Exception:
        return None
    return pid if daemon_alive(pid) else None

def write_daemon_pid(pid, args=None):
    HOME.mkdir(parents=True, exist_ok=True)
    DAEMON_PID.write_text(str(int(pid)))
    if args is not None:
        DAEMON_ARGS.write_text("\n".join(str(x) for x in args) + "\n")

def clear_daemon_pid():
    DAEMON_PID.unlink(missing_ok=True)

def ensure_public_server(path=None):
    """Rewrite settings.json if it still points at a dead ephemeral tunnel."""
    s = load_settings(path)
    # load_settings already normalizes + may rewrite; force another save for legacy copies
    save_settings(s, path)
    return s["server"]
