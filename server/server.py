#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib, hmac, json, os, re, secrets, sqlite3, sys, threading, time, urllib.error, urllib.request, uuid
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

def _load_dotenv():
    for candidate in (Path.cwd() / ".env", Path(__file__).parent.parent / ".env"):
        if not candidate.exists(): continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))
        break

_load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from verify import bucket_hash, weight, weight_sequence, flags, friction, friction_timeline, validate_allowlist, GENESIS, billable_secs
import install_sig


def oauth_config(provider):
    if provider == "github":
        return {"client_id": os.environ.get("GITHUB_CLIENT_ID", ""), "client_secret": os.environ.get("GITHUB_CLIENT_SECRET", ""),
                "base": os.environ.get("GITHUB_OAUTH_BASE", "https://github.com"), "api": os.environ.get("GITHUB_API_BASE", "https://api.github.com")}
    return {"client_id": os.environ.get("SLACK_CLIENT_ID", ""), "client_secret": os.environ.get("SLACK_CLIENT_SECRET", ""),
            "base": os.environ.get("SLACK_OAUTH_BASE", "https://slack.com"), "api": os.environ.get("SLACK_OAUTH_BASE", "https://slack.com")}

def http_json(url, data=None, headers=None, form=False):
    body = None
    hdrs = {"Accept": "application/json", "User-Agent": "struggletime", **(headers or {})}
    if data is not None:
        from urllib.parse import urlencode
        body = urlencode(data).encode() if form else json.dumps(data).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=12) as x: return json.loads(x.read() or b"{}")

def oauth_redirect(provider):
    base = os.environ.get("OAUTH_REDIRECT_BASE", "http://127.0.0.1:18765").rstrip("/")
    return f"{base}/v1/auth/{provider}/callback"

def valid_desktop_redirect(url):
    """Only allow tray/GUI callbacks on loopback http (no open redirects)."""
    try:
        u = urlparse((url or "").strip())
    except Exception:
        return False
    if u.scheme != "http" or u.username or u.password:
        return False
    if (u.hostname or "").lower() not in ("127.0.0.1", "localhost"):
        return False
    if (u.path or "/") not in ("/", "/callback"):
        return False
    return True

def append_query(url, **params):
    base = urlparse(url)
    q = parse_qs(base.query, keep_blank_values=True)
    for k, v in params.items():
        if v is not None:
            q[k] = [str(v)]
    from urllib.parse import urlencode
    flat = [(k, v) for k, vs in q.items() for v in vs]
    return base._replace(query=urlencode(flat)).geturl()

def oauth_identity(provider, code):
    cfg = oauth_config(provider)
    if provider == "github":
        tok = http_json(cfg["base"] + "/login/oauth/access_token",
                        {"client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                         "code": code, "redirect_uri": oauth_redirect(provider)})
        if "access_token" not in tok: raise ValueError("token exchange: " + str(tok.get("error") or tok.get("error_description") or "no access token"))
        user = http_json(cfg["api"] + "/user", headers={"Authorization": f"Bearer {tok['access_token']}"})
        if "login" not in user: raise ValueError("user fetch: " + str(user.get("message") or "no login"))
        return {"login": user["login"], "id": str(user["id"]), "access_token": tok["access_token"],
                "avatar": user.get("avatar_url") or "", "name": user.get("name") or user["login"]}
    tok = http_json(cfg["base"] + "/api/openid.connect.token",
                    {"client_id": cfg["client_id"], "client_secret": cfg["client_secret"], "code": code,
                     "grant_type": "authorization_code", "redirect_uri": oauth_redirect(provider)}, form=True)
    if "access_token" not in tok: raise ValueError("token exchange: " + str(tok.get("error") or tok.get("error_description") or "no access token"))
    info = http_json(cfg["api"] + "/api/openid.connect.userInfo", headers={"Authorization": f"Bearer {tok['access_token']}"})
    if "sub" not in info: raise ValueError("userInfo: " + str(info.get("error") or "no sub"))
    return {"login": info.get("name") or info.get("https://slack.com/user_id") or info["sub"], "id": str(info["sub"]),
            "access_token": tok["access_token"], "avatar": info.get("picture") or "",
            "name": info.get("name") or info.get("https://slack.com/user_id") or info["sub"]}

def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()

def mint_api_key():
    """Return (plaintext, sha256_hex, display_prefix). Plaintext shown once at mint/regenerate."""
    raw = "st_" + secrets.token_urlsafe(32)
    return raw, hash_token(raw), raw[:11] + "…"

def session_cookie(token="", *, clear=False):
    """Set/clear st_session. Secure only for https public bases (not plain IP:port)."""
    if clear or not token:
        base = "st_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
    else:
        base = f"st_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"
    pub = (os.environ.get("OAUTH_REDIRECT_BASE") or "").lower()
    if pub.startswith("https://"):
        base += "; Secure"
    return base

def mint_session(c, *, handle, slack_user_id, slack_avatar="", slack_name="", days=30):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    c.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?)",
              (hash_token(token), handle, slack_user_id, slack_avatar or "", slack_name or "", now, now + days * 86400))
    code = secrets.token_urlsafe(18)
    c.execute("INSERT INTO auth_code VALUES (?,?,?)", (code, token, now + 300))
    return token, code

def link_row(c, handle):
    return c.execute("SELECT * FROM link WHERE handle=?", (handle,)).fetchone()

def upsert_link(c, handle, **fields):
    row = link_row(c, handle)
    cur = {
        "github_login": row["github_login"] if row else None,
        "slack_user_id": row["slack_user_id"] if row else None,
        "github_token": row["github_token"] if row and "github_token" in row.keys() else None,
        "slack_avatar": row["slack_avatar"] if row and "slack_avatar" in row.keys() else None,
        "slack_name": row["slack_name"] if row and "slack_name" in row.keys() else None,
    }
    cur.update({k: v for k, v in fields.items() if v is not None})
    c.execute("""INSERT INTO link(handle, github_login, slack_user_id, github_token, slack_avatar, slack_name)
                 VALUES (?,?,?,?,?,?)
                 ON CONFLICT(handle) DO UPDATE SET
                   github_login=excluded.github_login,
                   slack_user_id=excluded.slack_user_id,
                   github_token=COALESCE(excluded.github_token, link.github_token),
                   slack_avatar=COALESCE(excluded.slack_avatar, link.slack_avatar),
                   slack_name=COALESCE(excluded.slack_name, link.slack_name)""",
              (handle, cur["github_login"], cur["slack_user_id"], cur["github_token"], cur["slack_avatar"], cur["slack_name"]))

REASONS = {"UNATTENDED_AGENT_DUMP","IDLE_INFLATION","NO_TEST_ITERATION","BOILERPLATE_HEAVY","CHAIN_BROKEN","GIT_MISMATCH","NONE"}
DB = os.environ.get("SERVER_DB", "server.db")
STATIC = Path(__file__).parent / "static"

_LOCK = threading.RLock(); _CONN = None

class _HeadSink:
    """Pass through response headers; discard the body for HTTP HEAD."""
    def __init__(self, real):
        self._real = real
        self._buf = b""
        self._headers_done = False
    def write(self, data):
        if data is None:
            return 0
        if isinstance(data, str):
            data = data.encode()
        if self._headers_done:
            return len(data)
        self._buf += data
        idx = self._buf.find(b"\r\n\r\n")
        if idx < 0:
            return len(data)
        self._real.write(self._buf[: idx + 4])
        self._headers_done = True
        self._buf = b""
        return len(data)
    def flush(self):
        if not self._headers_done and self._buf:
            self._real.write(self._buf)
            self._buf = b""
            self._headers_done = True
        self._real.flush()
    def __getattr__(self, name):
        return getattr(self._real, name)

def db():
    global _CONN
    if _CONN is not None: return _CONN
    c = sqlite3.connect(DB, timeout=10, check_same_thread=False); c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE IF NOT EXISTS bucket(
      bucket_id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT, bucket_start_wall INT,
      bucket_secs INT, state TEXT, weight REAL, weighted_secs REAL, hash TEXT, prev_hash TEXT, payload TEXT);
    CREATE TABLE IF NOT EXISTS chain_head(project_id TEXT PRIMARY KEY, last_hash TEXT);
    CREATE TABLE IF NOT EXISTS submission(
      id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT, claimed_weighted_secs REAL,
      ai_declaration TEXT, status TEXT, flags TEXT);
    CREATE TABLE IF NOT EXISTS review(
      id TEXT PRIMARY KEY, submission_id TEXT, reviewer_id TEXT,
      approved_weighted_secs REAL, reason_code TEXT);
    CREATE TABLE IF NOT EXISTS user(
      handle TEXT PRIMARY KEY, token_hash TEXT, is_public INT NOT NULL DEFAULT 1, created_at INT);
    CREATE TABLE IF NOT EXISTS silent_streak(project_id TEXT PRIMARY KEY, silent_secs INT NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS link(handle TEXT PRIMARY KEY, github_login TEXT, slack_user_id TEXT);
    CREATE TABLE IF NOT EXISTS integration_event(id TEXT PRIMARY KEY, kind TEXT, text TEXT, delivered INT NOT NULL DEFAULT 0, created_at INT);
    CREATE TABLE IF NOT EXISTS github_event(id TEXT PRIMARY KEY, project_id TEXT, repo TEXT, commits INT, head TEXT);
    CREATE TABLE IF NOT EXISTS oauth_state(state TEXT PRIMARY KEY, provider TEXT, handle TEXT, created_at INT);
    CREATE TABLE IF NOT EXISTS project(
      id TEXT PRIMARY KEY, owner TEXT, name TEXT, repo_url TEXT, head_from TEXT, head_to TEXT);
    CREATE TABLE IF NOT EXISTS session(
      token_hash TEXT PRIMARY KEY, handle TEXT, slack_user_id TEXT NOT NULL,
      slack_avatar TEXT, slack_name TEXT, created_at INT, expires_at INT);
    CREATE TABLE IF NOT EXISTS auth_code(code TEXT PRIMARY KEY, token TEXT NOT NULL, expires_at INT NOT NULL);
    """)
    cols = {r[1] for r in c.execute("PRAGMA table_info(link)")}
    for col in ("github_token", "slack_avatar", "slack_name"):
        if col not in cols: c.execute(f"ALTER TABLE link ADD COLUMN {col} TEXT")
    ocols = {r[1] for r in c.execute("PRAGMA table_info(oauth_state)")}
    if "intent" not in ocols: c.execute("ALTER TABLE oauth_state ADD COLUMN intent TEXT DEFAULT 'link'")
    if "desktop_redirect" not in ocols: c.execute("ALTER TABLE oauth_state ADD COLUMN desktop_redirect TEXT DEFAULT ''")
    ucols = {r[1] for r in c.execute("PRAGMA table_info(user)")}
    for col, spec in (
        ("display_name", "TEXT"),
        ("email", "TEXT"),
        ("country", "TEXT"),
        ("timezone", "TEXT"),
        ("show_leaderboards", "INT NOT NULL DEFAULT 1"),
        ("api_key_prefix", "TEXT"),
        ("weekly_goal_hours", "REAL NOT NULL DEFAULT 5.0"),
    ):
        if col not in ucols: c.execute(f"ALTER TABLE user ADD COLUMN {col} {spec}")
    c.execute("""
    CREATE TABLE IF NOT EXISTS install_key(
      install_id TEXT PRIMARY KEY, pubkey TEXT NOT NULL, user_id TEXT, created_at INT)
    """)
    pcols = {r[1] for r in c.execute("PRAGMA table_info(project)")}
    for col, spec in (
        ("archived", "INT NOT NULL DEFAULT 0"),
        ("deleted", "INT NOT NULL DEFAULT 0"),
        ("last_repo_url", "TEXT"),
        ("local_path", "TEXT"),
    ):
        if col not in pcols: c.execute(f"ALTER TABLE project ADD COLUMN {col} {spec}")
    c.commit()
    _CONN = c; return c

def notify(c, text):
    eid = str(uuid.uuid4()); delivered = 0
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if url:
        try:
            req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
            delivered = 1 if urllib.request.urlopen(req, timeout=3).status < 300 else 0
        except Exception: delivered = 0
    c.execute("INSERT INTO integration_event VALUES (?,?,?,?,?)", (eid, "slack", text, delivered, int(time.time())))
    return delivered

def stats_for(c, project=None, user=None):
    q, args = "SELECT * FROM bucket WHERE 1=1", []
    if project: q += " AND project_id=?"; args.append(project)
    if user: q += " AND user_id=?"; args.append(user)
    rows = c.execute(q, args).fetchall()
    weighted = sum(r["weighted_secs"] for r in rows)
    raw = sum(r["bucket_secs"] for r in rows)
    by_state, days = {}, set()
    frictions = []
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
        days.add(date.fromtimestamp(r["bucket_start_wall"]).isoformat())
        try: frictions.append(friction(json.loads(r["payload"])))
        except Exception: pass
    streak, d = 0, date.today()
    while d.isoformat() in days: streak += 1; d -= timedelta(days=1)
    buckets = [json.loads(r["payload"]) for r in rows]
    today = date.today()
    week_cut = (today - timedelta(days=6)).isoformat()
    today_s = today.isoformat()
    weighted_today = sum(r["weighted_secs"] for r in rows if date.fromtimestamp(r["bucket_start_wall"]).isoformat() == today_s)
    weighted_week = sum(r["weighted_secs"] for r in rows if date.fromtimestamp(r["bucket_start_wall"]).isoformat() >= week_cut)
    n = len(rows) or 1
    loop_n = by_state.get("CLOSED_LOOP", 0) + by_state.get("STRUGGLE_LOOP", 0) + by_state.get("AI_STRUGGLE", 0)
    dump_n = by_state.get("OPEN_LOOP_AGENT_DUMP", 0)
    projects_n = len({r["project_id"] for r in rows})
    return {"buckets": len(rows), "raw_secs": raw,
            "weighted_secs": weighted, "weighted_hours": round(weighted / 3600, 4),
            "weighted_today_secs": weighted_today, "weighted_today_hours": round(weighted_today / 3600, 4),
            "weighted_week_secs": weighted_week, "weighted_week_hours": round(weighted_week / 3600, 4),
            "trust_pct": round(100 * weighted / raw) if raw else 0,
            "loop_pct": round(100 * loop_n / n) if rows else 0,
            "dump_pct": round(100 * dump_n / n) if rows else 0,
            "avg_friction": round(sum(frictions) / len(frictions), 3) if frictions else 0,
            "active_projects": projects_n,
            "by_state": by_state, "active_days": len(days), "current_streak_days": streak,
            "flags": flags(buckets, weighted / 3600) if buckets else []}

STATE_LABELS = {
    "AI_STRUGGLE": "ai struggle",
    "CLOSED_LOOP": "closed loop",
    "STRUGGLE_LOOP": "struggle loop",
    "OPEN_LOOP_AGENT_DUMP": "agent dump",
    "AI_REVIEWED": "ai reviewed",
    "SILENT_DEBUG": "silent debug",
    "HUMAN_UNVERIFIED": "human unverified",
    "AI_UNREVIEWED": "ai unverified",
    "IDLE": "idle",
    "EXEC_ONLY": "exec only",
}

def _local_today(tz_offset=0):
    """Calendar date in the viewer's timezone (tz_offset = minutes east of UTC)."""
    return (datetime.now(timezone.utc) + timedelta(minutes=int(tz_offset or 0))).date()

def _range_cut(range_key, tz_offset=0):
    today = _local_today(tz_offset)
    if range_key in ("today", "1d", "24h"):
        return today.isoformat(), "today"
    if range_key in ("30d", "month"):
        return (today - timedelta(days=29)).isoformat(), "last 30 days"
    if range_key in ("all", "lifetime"):
        return None, "all time"
    return (today - timedelta(days=6)).isoformat(), "last 7 days"

def _payload_share(mapping, weight_secs):
    """Split weighted secs across label→count shares from a bucket payload."""
    if not isinstance(mapping, dict) or not mapping:
        return {}
    total = sum(max(0, int(v or 0)) for v in mapping.values()) or 1
    out = {}
    for k, v in mapping.items():
        label = str(k or "unknown").lower().replace(" ", "-")[:32]
        if not label or label == "untracked":
            continue
        out[label] = out.get(label, 0.0) + weight_secs * (max(0, int(v or 0)) / total)
    return out

def build_analytics(c, user=None, range_key="7d", project=None, category=None, language=None, os_name=None, editor=None, tz_offset=0):
    """Dashboard analytics from real buckets — os/language/editor from daemon payloads."""
    cut, label = _range_cut(range_key, tz_offset)
    q, args = "SELECT * FROM bucket WHERE 1=1", []
    if user:
        q += " AND user_id=?"; args.append(user)
    if project and project not in ("", "all"):
        q += " AND project_id=?"; args.append(project)
    rows = c.execute(q, args).fetchall()

    def local_parts(ts):
        local_dt = datetime.fromtimestamp(ts, tz=timezone.utc) + timedelta(minutes=int(tz_offset))
        d = local_dt.date()
        return d.isoformat(), d.weekday() % 7, local_dt.hour

    # Ignore legacy placeholder filter values so old localStorage doesn't blank the dashboard
    if language in ("untracked", "local-daemon", "local_daemon"):
        language = "all"
    if os_name in ("untracked",):
        os_name = "all"
    if editor in ("local-daemon", "local_daemon", "untracked"):
        editor = "all"

    filtered = []
    for r in rows:
        day, wd, hour = local_parts(r["bucket_start_wall"])
        if cut and day < cut:
            continue
        if category and category not in ("", "all") and r["state"] != category:
            continue
        try:
            payload = json.loads(r["payload"])
        except Exception:
            payload = {}
        langs = payload.get("languages") or {}
        editors_map = payload.get("editors") or {}
        # Older buckets omit os/languages — still count them as unknown so dashboards aren't blank
        if not langs:
            langs = {"unknown": 1}
        if not editors_map:
            editors_map = {"unknown": 1}
        os_id = (payload.get("os") or (payload.get("os_detail") or {}).get("id") or "unknown")
        os_id = str(os_id).lower().replace(" ", "-")
        if os_id in ("", "untracked"):
            os_id = "unknown"
        if language and language not in ("", "all"):
            if language not in langs and not any(str(k).lower() == language for k in langs):
                continue
        if os_name and os_name not in ("", "all"):
            if os_id != os_name:
                continue
        if editor and editor not in ("", "all"):
            if editor not in editors_map and not any(str(k).lower() == editor for k in editors_map):
                continue
        # stash normalized maps for aggregation
        payload = dict(payload)
        payload["languages"] = langs
        payload["editors"] = editors_map
        filtered.append((r, day, wd, hour, payload, os_id))

    proj_meta = {row["id"]: dict(row) for row in c.execute("SELECT * FROM project")}
    by_project, by_state, by_day = {}, {}, {}
    by_language, by_os, by_editor = {}, {}, {}
    rhythm = [[0.0 for _ in range(24)] for _ in range(7)]
    human_w = ai_w = 0.0
    total_w = 0.0
    today_s = _local_today(tz_offset).isoformat()
    today_w = 0.0
    day_totals = {}

    for r, day, wd, hour, payload, os_id in filtered:
        w = float(r["weighted_secs"] or 0)
        total_w += w
        by_project[r["project_id"]] = by_project.get(r["project_id"], 0) + w
        by_state[r["state"]] = by_state.get(r["state"], 0) + w
        by_day[day] = by_day.get(day, 0) + w
        day_totals[day] = day_totals.get(day, 0) + w
        rhythm[wd][hour] += w / 3600
        if day == today_s:
            today_w += w
        for k, share in _payload_share(payload.get("languages") or {}, w).items():
            by_language[k] = by_language.get(k, 0) + share
        for k, share in _payload_share(payload.get("editors") or {}, w).items():
            by_editor[k] = by_editor.get(k, 0) + share
        if os_id and os_id != "untracked":
            by_os[os_id] = by_os.get(os_id, 0) + w
        try:
            counts = payload.get("counts") or {}
            he = float(counts.get("human_edits") or 0)
            aw = float(counts.get("ai_writes") or 0)
            split = he + aw
            if split > 0:
                human_w += w * (he / split)
                ai_w += w * (aw / split)
            else:
                human_w += w
        except Exception:
            human_w += w

    def ranked(mapping, name_fn):
        items = sorted(mapping.items(), key=lambda x: -x[1])
        out = []
        for key, secs in items:
            out.append({"id": key, "label": name_fn(key), "secs": round(secs, 2), "hours": round(secs / 3600, 4),
                        "pct": round(100 * secs / total_w, 1) if total_w else 0})
        return out

    projects = ranked(by_project, lambda pid: (proj_meta.get(pid) or {}).get("name") or pid)
    categories = ranked(by_state, lambda s: STATE_LABELS.get(s, (s or "unknown").replace("_", " ").lower()))
    languages = ranked(by_language, lambda s: s)
    oses = ranked(by_os, lambda s: s)
    editors = ranked(by_editor, lambda s: s.replace("-", " "))

    series = [{"day": d, "hours": round(by_day.get(d, 0) / 3600, 4)} for d in sorted(by_day)]
    prior_days = [v for d, v in day_totals.items() if d != today_s]
    baseline = (sum(prior_days) / len(prior_days)) if prior_days else 0.0
    pace = {
        "today_hours": round(today_w / 3600, 4),
        "baseline_hours": round(baseline / 3600, 4),
        "delta_pct": round(100 * (today_w - baseline) / baseline, 1) if baseline else (100.0 if today_w else 0.0),
    }
    modes = {
        "human_hours": round(human_w / 3600, 4),
        "ai_hours": round(ai_w / 3600, 4),
        "human_pct": round(100 * human_w / (human_w + ai_w), 1) if (human_w + ai_w) else 0,
        "ai_pct": round(100 * ai_w / (human_w + ai_w), 1) if (human_w + ai_w) else 0,
    }

    filter_projects = [{"id": "all", "label": "all projects"}] + [
        {"id": p["id"], "label": p["name"] or p["id"]} for p in sorted(proj_meta.values(), key=lambda x: (x.get("name") or x["id"]))
        if (not user) or p.get("owner") == user or p["id"] in by_project
    ]
    if user:
        owned = [dict(r) for r in c.execute("SELECT * FROM project WHERE owner=?", (user,))]
        seen = {x["id"] for x in filter_projects}
        for p in owned:
            if p["id"] not in seen:
                filter_projects.append({"id": p["id"], "label": p["name"] or p["id"]})

    # Recent logged buckets (legacy-safe) for the activity list — newest first
    recent = []
    for r, day, _wd, _hour, payload, os_id in sorted(filtered, key=lambda x: -int(x[0]["bucket_start_wall"] or 0))[:40]:
        eds = payload.get("editors") or {}
        ags = payload.get("agents") or {}
        langs = payload.get("languages") or {}
        recent.append({
            "bucket_id": r["bucket_id"],
            "project_id": r["project_id"],
            "project_label": (proj_meta.get(r["project_id"]) or {}).get("name") or r["project_id"],
            "day": day,
            "state": r["state"],
            "state_label": STATE_LABELS.get(r["state"], (r["state"] or "unknown").replace("_", " ").lower()),
            "weighted_secs": round(float(r["weighted_secs"] or 0), 2),
            "weighted_hours": round(float(r["weighted_secs"] or 0) / 3600, 4),
            "bucket_secs": int(r["bucket_secs"] or 0),
            "os": os_id or "unknown",
            "editor": next(iter(eds.keys()), "unknown"),
            "agent": next(iter(ags.keys()), "human"),
            "language": next(iter(langs.keys()), "unknown"),
        })

    total_hours = round(total_w / 3600, 4)
    return {
        "range": range_key, "range_label": label, "tz_offset": int(tz_offset),
        "total_secs": round(total_w, 2), "total_hours": total_hours,
        # Alias used by some clients / older UI copies that read weighted_hours
        "weighted_hours": total_hours,
        "bucket_count": len(filtered),
        "top_project": projects[0] if projects else None,
        "top_language": languages[0] if languages else None,
        "top_os": oses[0] if oses else None,
        "top_editor": editors[0] if editors else None,
        "top_category": categories[0] if categories else None,
        "projects": projects, "languages": languages, "oses": oses, "editors": editors, "categories": categories,
        "series": series, "rhythm": rhythm, "pace": pace, "modes": modes, "recent": recent,
        "filters": {
            "projects": filter_projects,
            "categories": [{"id": "all", "label": "all modes"}] + [{"id": c["id"], "label": c["label"]} for c in categories],
            "languages": [{"id": "all", "label": "all languages"}] + [{"id": x["id"], "label": x["label"]} for x in languages],
            "oses": [{"id": "all", "label": "all OS"}] + [{"id": x["id"], "label": x["label"]} for x in oses],
            "editors": [{"id": "all", "label": "all editors"}] + [{"id": x["id"], "label": x["label"]} for x in editors],
            "ranges": [
                {"id": "today", "label": "today"}, {"id": "7d", "label": "7 days"},
                {"id": "30d", "label": "30 days"}, {"id": "all", "label": "all time"},
            ],
        },
    }

def slugify(name):
    return re.sub(r"[^a-z0-9_-]+", "-", (name or "").lower()).strip("-")[:40] or "project"

def repo_slug(url):
    if not url: return None
    m = re.search(r"github\.com[/:]([^/]+)/([^/#?]+)", url or "")
    if not m: return None
    return f"{m.group(1)}/{re.sub(r'\.git$', '', m.group(2))}"

def _urow(u, key, default=None):
    try: return u[key] if key in u.keys() and u[key] is not None else default
    except Exception: return default

def profile_payload(c, handle, session=None):
    u = c.execute("SELECT * FROM user WHERE handle=?", (handle,)).fetchone()
    if not u: return None
    link = link_row(c, handle)
    return {"handle": handle, "is_public": bool(u["is_public"]),
            "display_name": _urow(u, "display_name") or (link["slack_name"] if link and "slack_name" in link.keys() else None),
            "email": _urow(u, "email"),
            "country": _urow(u, "country"),
            "timezone": _urow(u, "timezone"),
            "show_leaderboards": bool(_urow(u, "show_leaderboards", 1)),
            "lb_opt_out": not bool(u["is_public"]),
            "github_login": link["github_login"] if link else None,
            "slack_user_id": link["slack_user_id"] if link else None,
            "slack_avatar": (link["slack_avatar"] if link else None) or (session["slack_avatar"] if session else None),
            "slack_name": (link["slack_name"] if link else None) or (session["slack_name"] if session else None),
            "github_linked": bool(link and link["github_login"]),
            "github_needs_reconnect": bool(
                link and link["github_login"]
                and not ((link["github_token"] if "github_token" in link.keys() else None) or "")
            ),
            "slack_linked": bool(link and link["slack_user_id"]),
            "needs_handle": False,
            "weekly_goal_hours": float(_urow(u, "weekly_goal_hours", 5.0) or 5.0),
            **stats_for(c, user=handle)}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send_json(self, code, obj, set_cookie=None):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        if set_cookie: self.send_header("Set-Cookie", set_cookie)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def raw(self):
        if not hasattr(self, "_raw"): self._raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        return self._raw
    def body(self):
        return json.loads(self.raw() or b"{}")
    def redirect(self, location):
        self.send_response(302); self.send_header("Location", location); self.end_headers()
    def _cookie_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "st_session" and v: return v
        return ""
    def _auth_candidates(self):
        """Authorization / X-Session-Token / cookie — prefer each until one authenticates."""
        out = []
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            out.append(auth[7:].strip())
        xs = (self.headers.get("X-Session-Token") or "").strip()
        if xs:
            out.append(xs)
        ck = self._cookie_token()
        if ck:
            out.append(ck)
        # de-dupe preserve order
        seen, uniq = set(), []
        for t in out:
            if t and t not in seen:
                seen.add(t); uniq.append(t)
        return uniq
    def _bearer(self):
        """First presented secret (compat). Prefer session()/auth_handle() for real auth."""
        cands = self._auth_candidates()
        return cands[0] if cands else ""
    def session(self, c, token=None):
        if token is not None:
            if not token: return None
            return c.execute("SELECT * FROM session WHERE token_hash=? AND expires_at>?",
                             (hash_token(token), int(time.time()))).fetchone()
        # Stale Bearer in localStorage must not mask a valid st_session cookie
        for tok in self._auth_candidates():
            row = c.execute("SELECT * FROM session WHERE token_hash=? AND expires_at>?",
                            (hash_token(tok), int(time.time()))).fetchone()
            if row: return row
        return None
    def api_key_user(self, c, token=None):
        if token is not None:
            if not token: return None
            return c.execute("SELECT * FROM user WHERE token_hash=? AND token_hash!='' AND token_hash IS NOT NULL",
                             (hash_token(token),)).fetchone()
        for tok in self._auth_candidates():
            row = c.execute("SELECT * FROM user WHERE token_hash=? AND token_hash!='' AND token_hash IS NOT NULL",
                            (hash_token(tok),)).fetchone()
            if row: return row
        return None
    def auth_handle(self, c):
        s = self.session(c)
        if s and s["handle"]: return s["handle"]
        u = self.api_key_user(c)
        return u["handle"] if u else None

    def do_GET(self):
        with _LOCK:
            try: return self._get()
            except (BrokenPipeError, ConnectionResetError): pass
            except Exception as e: return self.send_json(500, {"error": f"internal: {type(e).__name__}"})
    def do_HEAD(self):
        """Cloudflare and browsers probe with HEAD — same headers as GET, no body."""
        with _LOCK:
            real = self.wfile
            self.wfile = _HeadSink(real)
            try:
                return self._get()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                return self.send_json(500, {"error": f"internal: {type(e).__name__}"})
            finally:
                self.wfile = real
    def _get(self):
        parsed = urlparse(self.path); p = parsed.path.strip("/").split("/") if parsed.path.strip("/") else []
        c = db()
        pages = ("home", "dashboard", "projects", "builders", "review", "settings", "login", "signout", "logout")
        if p == ["integrations"]:
            self.send_response(302); self.send_header("Location", "/settings"); self.end_headers(); return
        if p == ["dashboard"]:
            self.send_response(302); self.send_header("Location", "/home"); self.end_headers(); return
        if p == [] or p == ["index.html"] or (len(p) == 1 and p[0] in pages):
            f = STATIC / "index.html"
            if not f.exists(): return self.send_json(404, {"error": "no dashboard"})
            # Bust browser caches so dashboard JS/CSS updates land without a manual hard-refresh
            def _asset_v(name):
                try: return str(int((STATIC / name).stat().st_mtime))
                except Exception: return "0"
            html = f.read_text(encoding="utf-8")
            for asset in ("styles.css", "ui.js", "app.js"):
                ver = _asset_v(asset)
                html = html.replace(f'href="/{asset}"', f'href="/{asset}?v={ver}"')
                html = html.replace(f'src="/{asset}"', f'src="/{asset}?v={ver}"')
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        if len(p) == 1 and p[0] in ("ui.js", "app.js", "styles.css"):
            f = STATIC / p[0]
            if not f.exists(): return self.send_json(404, {"error": "not found"})
            ctype = "application/javascript" if p[0].endswith(".js") else "text/css"
            body = f.read_bytes(); self.send_response(200)
            self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(body); return
        if len(p) == 2 and p[0] == "img" and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}\.(svg|png|webp|jpe?g)", p[1], re.I):
            f = STATIC / "img" / p[1]
            if not f.exists(): return self.send_json(404, {"error": "not found"})
            ext = f.suffix.lower()
            ctype = {".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(ext, "application/octet-stream")
            body = f.read_bytes(); self.send_response(200)
            self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        if p == ["health"]: return self.send_json(200, {"ok": True})
        if p == ["v1", "auth", "status"]:
            gh, sl = oauth_config("github"), oauth_config("slack")
            base = os.environ.get("OAUTH_REDIRECT_BASE", "").strip()
            return self.send_json(200, {
                "github_client_id_set": bool(gh["client_id"]), "github_client_secret_set": bool(gh["client_secret"]),
                "slack_client_id_set": bool(sl["client_id"]), "slack_client_secret_set": bool(sl["client_secret"]),
                "github_ready": bool(gh["client_id"] and gh["client_secret"]),
                "slack_ready": bool(sl["client_id"] and sl["client_secret"]),
                "slack_signing_set": bool(os.environ.get("SLACK_SIGNING_SECRET")),
                "slack_webhook_set": bool(os.environ.get("SLACK_WEBHOOK_URL")),
                "oauth_redirect_base_set": bool(base),
                "github_callback": oauth_redirect("github"), "slack_callback": oauth_redirect("slack")})
        if p == ["v1", "auth", "me"]:
            s = self.session(c)
            if s:
                if not s["handle"]:
                    return self.send_json(200, {"needs_handle": True, "slack_user_id": s["slack_user_id"],
                                                "slack_avatar": s["slack_avatar"], "slack_name": s["slack_name"],
                                                "github_linked": False, "slack_linked": True})
                return self.send_json(200, profile_payload(c, s["handle"], s))
            u = self.api_key_user(c)
            if u:
                return self.send_json(200, {**profile_payload(c, u["handle"]), "auth": "api_key"})
            return self.send_json(401, {"error": "not signed in"})
        if p == ["v1", "auth", "api-key"]:
            # status for the signed-in web session (never returns the raw key)
            s = self.session(c)
            if not s or not s["handle"]:
                return self.send_json(401, {"error": "sign in with slack first"})
            u = c.execute("SELECT token_hash, api_key_prefix FROM user WHERE handle=?", (s["handle"],)).fetchone()
            configured = bool(u and u["token_hash"])
            prefix = (u["api_key_prefix"] if u and "api_key_prefix" in u.keys() else None) or None
            return self.send_json(200, {"configured": configured, "prefix": prefix if configured else None})
        if len(p) == 3 and p[:2] == ["v1", "handles"]:
            handle = p[2].lower()
            taken = c.execute("SELECT 1 FROM user WHERE handle=?", (handle,)).fetchone()
            valid = bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,30}", handle))
            return self.send_json(200, {"handle": handle, "valid": valid, "available": valid and not taken})
        if len(p) == 4 and p[:2] == ["v1", "auth"] and p[2] in ("github", "slack") and p[3] in ("start", "callback"):
            provider = p[2]; cfg = oauth_config(provider)
            qs = parse_qs(parsed.query)
            if p[3] == "start":
                # If OAuth is registered on a public tunnel, never start the flow from loopback —
                # bounce to the public host so cookies/callbacks stay on the same origin.
                public = os.environ.get("OAUTH_REDIRECT_BASE", "").rstrip("/")
                host = (self.headers.get("Host") or "").split(":")[0].lower()
                pub_host = (urlparse(public).hostname or "").lower() if public else ""
                # Only bounce loopback → remote tunnel. Same-host public (local tests) must proceed.
                if public and host in ("127.0.0.1", "localhost", "::1") and pub_host not in ("", "127.0.0.1", "localhost", "::1"):
                    q = ("?" + parsed.query) if parsed.query else ""
                    return self.redirect(public + "/v1/auth/" + provider + "/start" + q)
                if not (cfg["client_id"] and cfg["client_secret"]):
                    return self.redirect(("/settings" if provider == "github" else "/login") + "?oauth=" + provider + "_not_configured")
                desktop = (qs.get("desktop_redirect", [""])[0] or "").strip()
                if desktop and not valid_desktop_redirect(desktop):
                    desktop = ""
                if provider == "slack":
                    intent, handle = "login", ""
                else:
                    intent = "link"
                    # Prefer ?session= when it is a real web session; fall back to cookie
                    # so a stale localStorage API key cannot block GitHub linking.
                    raw_sess = (qs.get("session", [""])[0] or "").strip()
                    sess = self.session(c, token=raw_sess or None) if raw_sess else None
                    if not sess or not sess["handle"]:
                        sess = self.session(c)
                    if not sess or not sess["handle"]:
                        return self.redirect("/login?oauth=sign_in_with_slack_first")
                    handle = sess["handle"]
                state = secrets.token_urlsafe(24)
                c.execute(
                    "INSERT INTO oauth_state(state, provider, handle, created_at, intent, desktop_redirect) VALUES (?,?,?,?,?,?)",
                    (state, provider, handle, int(time.time()), intent, desktop or ""),
                ); c.commit()
                if provider == "github":
                    loc = (cfg["base"] + "/login/oauth/authorize?client_id=" + cfg["client_id"]
                           + "&scope=read:user%20repo&state=" + state
                           + "&redirect_uri=" + quote(oauth_redirect(provider), safe=""))
                else:
                    loc = (cfg["base"] + "/openid/connect/authorize?scope=openid%20profile&response_type=code&client_id="
                           + cfg["client_id"] + "&state=" + state
                           + "&redirect_uri=" + quote(oauth_redirect(provider), safe=""))
                return self.redirect(loc)
            if p[3] == "callback":
                fail_dest = "/settings" if provider == "github" else "/login"
                if qs.get("error", [""])[0]:
                    return self.redirect(fail_dest + "?oauth=" + provider + "_denied")
                state = qs.get("state", [""])[0]; code = qs.get("code", [""])[0]
                row = c.execute("SELECT * FROM oauth_state WHERE state=? AND provider=?", (state, provider)).fetchone()
                if not row or int(time.time()) - row["created_at"] > 600:
                    return self.redirect(fail_dest + "?oauth=bad_state")
                intent = row["intent"] if "intent" in row.keys() and row["intent"] else "link"
                c.execute("DELETE FROM oauth_state WHERE state=?", (state,)); c.commit()
                try: ident = oauth_identity(provider, code)
                except Exception as e:
                    print(f"oauth {provider} callback failed: {type(e).__name__}: {e}", file=sys.stderr)
                    return self.redirect(fail_dest + "?oauth=" + provider + "_failed")
                if provider == "slack":
                    existing = c.execute("SELECT handle FROM link WHERE slack_user_id=?", (ident["id"],)).fetchone()
                    handle = existing["handle"] if existing else None
                    if handle:
                        upsert_link(c, handle, slack_user_id=ident["id"], slack_avatar=ident["avatar"], slack_name=ident["name"])
                    _, auth_code = mint_session(c, handle=handle, slack_user_id=ident["id"],
                                                slack_avatar=ident["avatar"], slack_name=ident["name"])
                    c.commit()
                    desktop = ""
                    if "desktop_redirect" in row.keys():
                        desktop = (row["desktop_redirect"] or "").strip()
                    if desktop and valid_desktop_redirect(desktop):
                        return self.redirect(append_query(
                            desktop, auth=auth_code,
                            oauth=("slack_linked" if handle else "claim_handle"),
                        ))
                    if handle:
                        return self.redirect("/home?auth=" + quote(auth_code) + "&oauth=slack_linked")
                    return self.redirect("/login?auth=" + quote(auth_code) + "&oauth=claim_handle")
                # github link — must already be signed in via slack session with handle
                handle = row["handle"]
                if not handle or not c.execute("SELECT 1 FROM user WHERE handle=?", (handle,)).fetchone():
                    return self.redirect("/login?oauth=sign_in_with_slack_first")
                upsert_link(c, handle, github_login=ident["login"], github_token=ident["access_token"])
                c.commit()
                return self.redirect("/settings?oauth=github_linked")
        if p == ["v1", "me", "analytics"]:
            who = self.auth_handle(c)
            if not who:
                return self.send_json(401, {"error": "not signed in"})
            qs = parse_qs(parsed.query)
            try: tz_offset = int((qs.get("tz_offset") or ["0"])[0])
            except ValueError: tz_offset = 0
            payload = build_analytics(
                c, user=who,
                range_key=(qs.get("range") or ["7d"])[0],
                project=(qs.get("project") or ["all"])[0],
                category=(qs.get("category") or ["all"])[0],
                language=(qs.get("language") or ["all"])[0],
                os_name=(qs.get("os") or ["all"])[0],
                editor=(qs.get("editor") or ["all"])[0],
                tz_offset=tz_offset,
            )
            payload["signed_in"] = bool(who)
            payload["handle"] = who
            return self.send_json(200, payload)
        if p == ["v1", "me", "github-repos"]:
            handle = self.auth_handle(c)
            if not handle: return self.send_json(401, {"error": "sign in with slack and claim a handle first"})
            link = link_row(c, handle)
            if not link or not link["github_login"]:
                return self.send_json(400, {"error": "connect github in settings first"})
            token = (link["github_token"] if "github_token" in link.keys() else None) or os.environ.get("GITHUB_TOKEN") or ""
            if not token:
                return self.send_json(401, {"error": "reconnect github to grant repo access", "reconnect": True})
            known = {r["repo_url"] for r in c.execute("SELECT repo_url FROM project WHERE repo_url IS NOT NULL")}
            known_ids = {r["id"] for r in c.execute("SELECT id FROM project")}
            try:
                api = oauth_config("github")["api"]
                hdrs = {"Accept": "application/vnd.github+json", "User-Agent": "struggletime",
                        "Authorization": f"Bearer {token}"}
                repos, page = [], 1
                while page <= 10:
                    chunk = http_json(f"{api}/user/repos?per_page=100&page={page}&sort=updated&affiliation=owner,collaborator,organization_member", headers=hdrs)
                    if not isinstance(chunk, list) or not chunk: break
                    repos.extend(chunk); page += 1
                    if len(chunk) < 100: break
            except urllib.error.HTTPError as e:
                # Expired / revoked OAuth token — clear it so Settings shows reconnect
                if e.code in (401, 403) and link and "github_token" in link.keys() and link["github_token"]:
                    try:
                        c.execute("UPDATE link SET github_token=NULL WHERE handle=?", (handle,))
                        c.commit()
                    except Exception:
                        pass
                return self.send_json(401 if e.code in (401, 403) else 502, {
                    "error": "github access expired — reconnect github in settings",
                    "reconnect": True,
                    "status": e.code,
                })
            except Exception as e:
                return self.send_json(502, {"error": f"github repos fetch failed: {type(e).__name__}: {e}"})
            out = []
            for r in repos:
                url = r.get("html_url") or f"https://github.com/{r.get('full_name')}"
                pid = slugify(r.get("name"))
                out.append({"id": pid, "name": r.get("name") or pid, "full_name": r.get("full_name"),
                            "repo_url": url, "private": bool(r.get("private")), "description": r.get("description") or "",
                            "updated_at": r.get("updated_at"), "already_added": url in known or pid in known_ids})
            return self.send_json(200, {"github_login": link["github_login"], "repos": out})
        if p == ["v1", "users"]:
            out = []
            for u in c.execute("SELECT handle,is_public FROM user WHERE is_public=1"):
                lk = link_row(c, u["handle"])
                out.append({"handle": u["handle"],
                            "github_login": lk["github_login"] if lk else None,
                            "slack_avatar": lk["slack_avatar"] if lk and "slack_avatar" in lk.keys() else None,
                            "slack_name": lk["slack_name"] if lk and "slack_name" in lk.keys() else None,
                            **stats_for(c, user=u["handle"])})
            return self.send_json(200, {"users": sorted(out, key=lambda x: -x["weighted_hours"])})
        if len(p) == 3 and p[:2] == ["v1", "users"]:
            u = c.execute("SELECT handle,is_public FROM user WHERE handle=?", (p[2],)).fetchone()
            if not u: return self.send_json(404, {"error": "user not found"})
            if not u["is_public"] and self.auth_handle(c) != p[2]:
                return self.send_json(403, {"error": "profile is private"})
            projs = [dict(r) for r in c.execute("SELECT * FROM project WHERE owner=?", (p[2],))]
            payload = profile_payload(c, p[2])
            payload["projects"] = projs
            return self.send_json(200, payload)
        if p == ["v1", "projects"]:
            who = self.auth_handle(c)
            if not who:
                # Empty list looks like "all projects deleted" — force the client to sign in
                return self.send_json(401, {"error": "not signed in", "projects": []})
            out = []
            for r in c.execute("SELECT * FROM project WHERE owner=?", (who,)):
                out.append({
                    **dict(r),
                    "registered": True,
                    "archived": bool(_urow(r, "archived", 0)),
                    "deleted": bool(_urow(r, "deleted", 0)),
                    "last_repo_url": _urow(r, "last_repo_url"),
                    "local_path": _urow(r, "local_path"),
                    "repo_slug": repo_slug(r["repo_url"]) or repo_slug(_urow(r, "last_repo_url")),
                    **stats_for(c, project=r["id"]),
                })
            known = {x["id"] for x in out}
            # Promote bucket-only project ids into the project table so they don't look "gone"
            for r in c.execute("SELECT DISTINCT project_id FROM bucket WHERE user_id=?", (who,)):
                pid = r[0]
                if pid in known:
                    continue
                try:
                    c.execute(
                        "INSERT OR IGNORE INTO project(id, owner, name, repo_url, head_from, head_to) VALUES (?,?,?,?,?,?)",
                        (pid, who, pid, None, None, None),
                    )
                    c.commit()
                except Exception:
                    pass
                row = c.execute("SELECT * FROM project WHERE id=?", (pid,)).fetchone()
                if row and row["owner"] == who:
                    out.append({
                        **dict(row),
                        "registered": True,
                        "archived": bool(_urow(row, "archived", 0)),
                        "deleted": bool(_urow(row, "deleted", 0)),
                        "last_repo_url": _urow(row, "last_repo_url"),
                        "local_path": _urow(row, "local_path"),
                        "repo_slug": repo_slug(row["repo_url"]) or repo_slug(_urow(row, "last_repo_url")),
                        **stats_for(c, project=pid),
                    })
                else:
                    out.append({"id": pid, "owner": who, "name": pid, "repo_url": None, "repo_slug": None,
                                "archived": False, "deleted": False, "last_repo_url": None, "local_path": None,
                                "registered": False, **stats_for(c, project=pid)})
                known.add(pid)
            return self.send_json(200, {"projects": out})
        if p == ["v1", "leaderboard"]:
            period = parse_qs(parsed.query).get("period", ["week"])[0]
            today = date.today()
            if period in ("today", "24h", "daily"):
                cut = today.isoformat()
                label = "last 24 hours"
            else:
                cut = (today - timedelta(days=6)).isoformat()
                label = "last 7 days"
            entries = []
            for u in c.execute("SELECT * FROM user WHERE is_public=1"):
                link = link_row(c, u["handle"])
                st = stats_for(c, user=u["handle"])
                hours = st["weighted_today_hours"] if period in ("today", "24h", "daily") else st["weighted_week_hours"]
                # recompute period hours precisely from buckets
                rows = c.execute("SELECT weighted_secs, bucket_start_wall FROM bucket WHERE user_id=?", (u["handle"],)).fetchall()
                if period in ("today", "24h", "daily"):
                    hours = round(sum(r["weighted_secs"] for r in rows if date.fromtimestamp(r["bucket_start_wall"]).isoformat() == today.isoformat()) / 3600, 4)
                else:
                    hours = round(sum(r["weighted_secs"] for r in rows if date.fromtimestamp(r["bucket_start_wall"]).isoformat() >= cut) / 3600, 4)
                if hours <= 0: continue
                active = c.execute("SELECT project_id FROM bucket WHERE user_id=? ORDER BY bucket_start_wall DESC LIMIT 1", (u["handle"],)).fetchone()
                entries.append({
                    "handle": u["handle"],
                    "display_name": _urow(u, "display_name"),
                    "country": _urow(u, "country"),
                    "slack_avatar": link["slack_avatar"] if link and "slack_avatar" in link.keys() else None,
                    "slack_name": link["slack_name"] if link and "slack_name" in link.keys() else None,
                    "github_login": link["github_login"] if link else None,
                    "weighted_hours": hours,
                    "streak": st["current_streak_days"],
                    "active_project": active["project_id"] if active else None,
                })
            entries.sort(key=lambda e: -e["weighted_hours"])
            for i, e in enumerate(entries, 1): e["rank"] = i
            return self.send_json(200, {"period": period, "label": label, "entries": entries, "total": len(entries)})
        if len(p) == 4 and p[:2] == ["v1", "projects"] and p[3] in ("buckets", "friction"):
            rows = c.execute("SELECT payload FROM bucket WHERE project_id=? ORDER BY bucket_start_wall", (p[2],)).fetchall()
            if not rows: return self.send_json(404, {"error": "project not found"})
            buckets = [json.loads(r["payload"]) for r in rows]
            if p[3] == "buckets":
                return self.send_json(200, {"project_id": p[2], "buckets": buckets,
                                            "evidence_bytes": len(json.dumps(buckets).encode())})
            tl = friction_timeline(buckets)
            commits = [{"at_bucket": t["bucket_id"], "head_commit": t["head_commit"]} for t in tl if t["commits"]]
            return self.send_json(200, {"project_id": p[2], "timeline": tl, "commit_anchors": commits,
                                        "evidence_bytes": len(json.dumps(buckets).encode()),
                                        "budget": "target 25-50 kb per submitted session, hard cap 50 kb/hour of metadata"})
        if len(p) == 4 and p[:2] == ["v1", "projects"] and p[3] == "stats":
            if not c.execute("SELECT 1 FROM bucket WHERE project_id=? LIMIT 1", (p[2],)).fetchone():
                return self.send_json(404, {"error": "project not found"})
            return self.send_json(200, {"project_id": p[2], **stats_for(c, project=p[2])})
        if len(p) == 4 and p[:2] == ["v1", "projects"] and p[3] == "chain":
            # Daemon continues an existing hash chain after a fresh local ledger.
            who = self.auth_handle(c)
            if not who: return self.send_json(401, {"error": "sign in required"})
            row = c.execute("SELECT * FROM project WHERE id=?", (p[2],)).fetchone()
            if row and row["owner"] and row["owner"] != who:
                return self.send_json(403, {"error": "not your project"})
            head = c.execute("SELECT last_hash FROM chain_head WHERE project_id=?", (p[2],)).fetchone()
            return self.send_json(200, {
                "project_id": p[2],
                "last_hash": head["last_hash"] if head else GENESIS,
                "genesis": GENESIS,
            })
        if len(p) == 4 and p[:2] == ["v1", "projects"] and p[3] == "stream":
            if not c.execute("SELECT 1 FROM bucket WHERE project_id=? LIMIT 1", (p[2],)).fetchone():
                return self.send_json(404, {"error": "project not found"})
            payload = f"data: {json.dumps({'project_id': p[2], **stats_for(c, project=p[2])})}\n\n".encode()
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache"); self.end_headers(); self.wfile.write(payload)
            if "once=1" not in parsed.query:
                _LOCK.release()
                try:
                    while True: time.sleep(15); self.wfile.write(b": keepalive\n\n"); self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError): pass
                finally: _LOCK.acquire()
            return
        if p == ["v1", "integrations", "events"]:
            rows = c.execute("SELECT id,kind,text,delivered,created_at FROM integration_event ORDER BY rowid DESC LIMIT 50").fetchall()
            return self.send_json(200, {"events": [dict(r) for r in rows]})
        if len(p) == 4 and p[:2] == ["v1", "projects"] and p[3] == "github":
            r = c.execute("SELECT * FROM project WHERE id=?", (p[2],)).fetchone()
            if not r or not r["repo_url"]: return self.send_json(404, {"error": "no repo linked"})
            m = re.search(r"github\.com[/:]([^/]+)/([^/#?]+)", r["repo_url"])
            if not m: return self.send_json(200, {"linked": False, "repo_url": r["repo_url"]})
            owner, repo = m.group(1), re.sub(r"\.git$", "", m.group(2))
            events = [dict(x) for x in c.execute("SELECT * FROM github_event WHERE project_id=?", (p[2],))]
            out = {"linked": True, "owner": owner, "repo": repo, "url": f"https://github.com/{owner}/{repo}", "webhook_events": events}
            if "fetch=1" in parsed.query:
                try:
                    req = urllib.request.Request(f"https://api.github.com/repos/{owner}/{repo}", headers={"Accept": "application/vnd.github+json", "User-Agent": "struggletime"})
                    if os.environ.get("GITHUB_TOKEN"): req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
                    with urllib.request.urlopen(req, timeout=5) as x: gh = json.loads(x.read())
                    out["github"] = {"stars": gh.get("stargazers_count"), "default_branch": gh.get("default_branch"), "pushed_at": gh.get("pushed_at")}
                except Exception as e: out["github_error"] = type(e).__name__
            return self.send_json(200, out)
        if p == ["v1", "overview"]:
            st = stats_for(c)
            subs = c.execute("SELECT status, COUNT(*) n FROM submission GROUP BY status").fetchall()
            return self.send_json(200, {**st, "users": c.execute("SELECT COUNT(*) FROM user").fetchone()[0],
                "projects_count": c.execute("SELECT COUNT(DISTINCT project_id) FROM bucket").fetchone()[0],
                "submissions": {r["status"]: r["n"] for r in subs},
                "pending_reviews": sum(r["n"] for r in subs if r["status"] == "pending")})
        if p == ["v1", "submissions"]:
            rows = c.execute("SELECT * FROM submission ORDER BY rowid DESC").fetchall()
            return self.send_json(200, {"submissions": [{**dict(r), "flags": json.loads(r["flags"]),
                "claimed_weighted_hours": round(r["claimed_weighted_secs"] / 3600, 4)} for r in rows]})
        if len(p) == 3 and p[:2] == ["v1", "submissions"]:
            r = c.execute("SELECT * FROM submission WHERE id=?", (p[2],)).fetchone()
            if not r: return self.send_json(404, {"error": "submission not found"})
            return self.send_json(200, {**dict(r), "flags": json.loads(r["flags"])})
        return self.send_json(404, {"error": "not found"})

    def do_POST(self):
        with _LOCK:
            try: return self._post()
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                return self.send_json(400, {"error": f"malformed request: {type(e).__name__}"})
            except (BrokenPipeError, ConnectionResetError): pass
            except Exception as e: return self.send_json(500, {"error": f"internal: {type(e).__name__}"})
    def _post(self):
        p = urlparse(self.path).path.strip("/").split("/"); c = db()
        if p == ["v1", "auth", "session"]:
            d = self.body(); code = (d.get("code") or "").strip()
            row = c.execute("SELECT * FROM auth_code WHERE code=?", (code,)).fetchone()
            if not row or row["expires_at"] < int(time.time()):
                return self.send_json(400, {"error": "auth code expired or invalid"})
            c.execute("DELETE FROM auth_code WHERE code=?", (code,)); c.commit()
            token = row["token"]
            s = c.execute("SELECT * FROM session WHERE token_hash=?", (hash_token(token),)).fetchone()
            if not s: return self.send_json(400, {"error": "session missing"})
            cookie = session_cookie(token)
            if s["handle"]:
                return self.send_json(200, {"token": token, **profile_payload(c, s["handle"], s)}, set_cookie=cookie)
            return self.send_json(200, {"token": token, "needs_handle": True, "slack_user_id": s["slack_user_id"],
                                        "slack_avatar": s["slack_avatar"], "slack_name": s["slack_name"],
                                        "github_linked": False, "slack_linked": True}, set_cookie=cookie)
        if p == ["v1", "auth", "logout"]:
            s = self.session(c)
            if s: c.execute("DELETE FROM session WHERE token_hash=?", (s["token_hash"],)); c.commit()
            return self.send_json(200, {"ok": True}, set_cookie=session_cookie(clear=True))
        if p == ["v1", "auth", "api-key"]:
            # mint / regenerate StruggleTime API key (shown once). Requires Slack session + claimed handle.
            s = self.session(c)
            if not s or not s["handle"]:
                return self.send_json(401, {"error": "sign in with slack and claim a handle first"})
            d = self.body() or {}
            if not d.get("confirm"):
                return self.send_json(400, {"error": "pass confirm:true to mint or regenerate the API key"})
            raw, th, prefix = mint_api_key()
            c.execute(
                "UPDATE user SET token_hash=?, api_key_prefix=? WHERE handle=?",
                (th, prefix, s["handle"]),
            )
            c.commit()
            return self.send_json(200, {
                "api_key": raw, "prefix": prefix, "handle": s["handle"],
                "hint": "copy now — it will not be shown again. paste into the tray or daemon GUI.",
            })
        if p == ["v1", "auth", "api-key", "revoke"]:
            s = self.session(c)
            if not s or not s["handle"]:
                return self.send_json(401, {"error": "sign in with slack first"})
            c.execute("UPDATE user SET token_hash='', api_key_prefix=NULL WHERE handle=?", (s["handle"],))
            c.commit()
            return self.send_json(200, {"ok": True, "configured": False})
        if p == ["v1", "auth", "claim-handle"]:
            s = self.session(c)
            if not s: return self.send_json(401, {"error": "sign in with slack first"})
            if s["handle"]: return self.send_json(400, {"error": "handle already claimed for this session"})
            d = self.body(); handle = (d.get("handle") or "").strip().lower()
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,30}", handle):
                return self.send_json(400, {"error": "handles are 2-31 chars: lowercase letters, numbers, - and _"})
            taken_slack = c.execute("SELECT handle FROM link WHERE slack_user_id=?", (s["slack_user_id"],)).fetchone()
            if taken_slack: return self.send_json(409, {"error": "this slack account already owns @" + taken_slack["handle"]})
            try:
                c.execute(
                    "INSERT INTO user(handle, token_hash, is_public, created_at) VALUES (?,?,?,?)",
                    (handle, "", 0 if d.get("is_public") is False else 1, int(time.time())),
                )
            except sqlite3.IntegrityError:
                return self.send_json(409, {"error": "handle taken, pick another"})
            upsert_link(c, handle, slack_user_id=s["slack_user_id"], slack_avatar=s["slack_avatar"], slack_name=s["slack_name"])
            c.execute("UPDATE session SET handle=? WHERE token_hash=?", (handle, s["token_hash"]))
            c.commit()
            return self.send_json(201, profile_payload(c, handle, s))
        if p == ["v1", "auth", "signin"]:
            return self.send_json(410, {"error": "handle login removed. sign in with slack, then claim a handle"})
        if p == ["v1", "me", "profile"]:
            who = self.auth_handle(c)
            if not who: return self.send_json(401, {"error": "sign in with slack first"})
            d = self.body()
            row = c.execute("SELECT * FROM user WHERE handle=?", (who,)).fetchone()
            if not row: return self.send_json(404, {"error": "user not found"})
            display_name = d.get("display_name") if "display_name" in d else _urow(row, "display_name")
            email = d.get("email") if "email" in d else _urow(row, "email")
            country = d.get("country") if "country" in d else _urow(row, "country")
            tz = d.get("timezone") if "timezone" in d else _urow(row, "timezone")
            if country is not None:
                country = str(country).strip().upper() or None
                if country and not re.fullmatch(r"[A-Z]{2}", country):
                    return self.send_json(400, {"error": "country must be ISO-3166 alpha-2"})
            if "lb_opt_out" in d:
                is_public = 0 if d.get("lb_opt_out") else 1
            else:
                is_public = row["is_public"]
            if "show_leaderboards" in d:
                show_lb = 1 if d.get("show_leaderboards") else 0
            else:
                show_lb = _urow(row, "show_leaderboards", 1)
            if email is not None:
                email = str(email).strip() or None
                if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                    return self.send_json(400, {"error": "invalid email"})
            if display_name is not None:
                display_name = str(display_name).strip()[:64] or None
            if tz is not None:
                tz = str(tz).strip()[:64] or None
            goal = _urow(row, "weekly_goal_hours", 5.0)
            if "weekly_goal_hours" in d:
                try:
                    goal = float(d.get("weekly_goal_hours"))
                except (TypeError, ValueError):
                    return self.send_json(400, {"error": "weekly_goal_hours must be a number"})
                if goal < 0 or goal > 168:
                    return self.send_json(400, {"error": "weekly_goal_hours must be between 0 and 168"})
            c.execute(
                "UPDATE user SET display_name=?, email=?, country=?, timezone=?, is_public=?, show_leaderboards=?, weekly_goal_hours=? WHERE handle=?",
                (display_name, email, country, tz, int(is_public), int(show_lb), goal, who),
            )
            c.commit()
            return self.send_json(200, profile_payload(c, who, self.session(c)))
        if p == ["v1", "me", "import"]:
            who = self.auth_handle(c)
            if not who: return self.send_json(401, {"error": "sign in with slack first"})
            d = self.body()
            source = (d.get("source") or "").strip().lower()
            # StruggleTime project import UI removed; keep WakaTime-compatible stub for ~/.wakatime.cfg pipelines
            if source == "struggletime":
                return self.send_json(410, {"error": "struggletime project import removed"})
            if source != "wakatime":
                return self.send_json(400, {"error": "source must be wakatime"})
            # TODO: wire backend endpoint/action — ingest WakaTime heartbeats into buckets
            return self.send_json(202, {
                "ok": True, "source": source, "status": "queued",
                "message": "wakatime import accepted; sync pipeline not wired yet",
            })
        if p == ["v1", "me", "delete"]:
            who = self.auth_handle(c)
            if not who: return self.send_json(401, {"error": "sign in with slack first"})
            d = self.body()
            confirm = (d.get("confirm") or "").strip()
            if confirm != who:
                return self.send_json(400, {"error": "type your handle to confirm deletion"})
            # TODO: wire backend endpoint/action — soft-delete + evidence retention policy
            c.execute("DELETE FROM session WHERE handle=?", (who,))
            c.execute("DELETE FROM link WHERE handle=?", (who,))
            c.execute("DELETE FROM project WHERE owner=?", (who,))
            c.execute("DELETE FROM user WHERE handle=?", (who,))
            c.commit()
            return self.send_json(200, {"ok": True, "deleted": who},
                                  set_cookie=session_cookie(clear=True))
        if p == ["v1", "users"]:
            # legacy create without slack is closed; use claim-handle after slack login
            return self.send_json(410, {"error": "claim a handle after signing in with slack (/v1/auth/claim-handle)"})
        if len(p) == 4 and p[:2] == ["v1", "users"] and p[3] in ("github", "slack"):
            # keep manual link endpoints for tests/automation, but require session owning the handle
            d = self.body(); handle = p[2]
            who = self.auth_handle(c)
            if who != handle:
                return self.send_json(401, {"error": "sign in as this handle via slack to link accounts"})
            if not c.execute("SELECT 1 FROM user WHERE handle=?", (handle,)).fetchone(): return self.send_json(404, {"error": "user not found"})
            if p[3] == "github":
                login = (d.get("github_login") or "").strip()
                if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", login): return self.send_json(400, {"error": "invalid github login"})
                upsert_link(c, handle, github_login=login, github_token=d.get("github_token"))
                c.commit(); return self.send_json(200, {"handle": handle, "github_login": login, "github_url": f"https://github.com/{login}"})
            sid = (d.get("slack_user_id") or "").strip()
            if not sid: return self.send_json(400, {"error": "slack_user_id required"})
            upsert_link(c, handle, slack_user_id=sid)
            c.commit(); return self.send_json(200, {"handle": handle, "slack_user_id": sid})
        if p == ["v1", "integrations", "github", "webhook"]:
            raw = self.raw(); secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
            if secret:
                expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected, self.headers.get("X-Hub-Signature-256", "")): return self.send_json(401, {"error": "bad signature"})
            try: payload = json.loads(raw or b"{}")
            except Exception: return self.send_json(400, {"error": "invalid json"})
            event = self.headers.get("X-GitHub-Event", "push")
            if event != "push": return self.send_json(200, {"accepted": False, "event": event})
            repo_name = (payload.get("repository") or {}).get("full_name", "")
            commits = payload.get("commits") or []
            proj = None
            for r in c.execute("SELECT id, repo_url FROM project"):
                if r["repo_url"] and repo_name and repo_name.lower() in r["repo_url"].lower(): proj = r["id"]
            head = (payload.get("after") or "")[:40]
            c.execute("INSERT INTO github_event VALUES (?,?,?,?,?)", (str(uuid.uuid4()), proj, repo_name, len(commits), head)); c.commit()
            return self.send_json(202, {"accepted": True, "project_id": proj, "commits": len(commits), "head": head})
        if p == ["v1", "integrations", "slack", "command"]:
            form = {k: v[0] for k, v in parse_qs(self.raw().decode()).items()}
            secret = os.environ.get("SLACK_SIGNING_SECRET")
            if secret:
                ts = self.headers.get("X-Slack-Request-Timestamp", ""); sig = self.headers.get("X-Slack-Signature", "")
                base = f"v0:{ts}:".encode() + self.raw()
                expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected, sig): return self.send_json(401, {"error": "bad signature"})
            text = (form.get("text") or "stats").strip(); slack_id = form.get("user_id", "")
            link = c.execute("SELECT handle FROM link WHERE slack_user_id=?", (slack_id,)).fetchone()
            parts = text.split(); handle = parts[1] if len(parts) > 1 else (link["handle"] if link else form.get("user_name", ""))
            u = c.execute("SELECT handle FROM user WHERE handle=?", (handle,)).fetchone()
            if not u: return self.send_json(200, {"response_type": "ephemeral", "text": f"no struggletime account for {handle or 'you'} yet. sign in with slack on the dashboard and claim a handle."})
            st = stats_for(c, user=handle)
            return self.send_json(200, {"response_type": "ephemeral", "text": f"@{handle}: {st['weighted_hours']} weighted h · streak {st['current_streak_days']}d · {st['buckets']} buckets tracked passively"})
        if p == ["v1", "projects"]:
            d = self.body()
            owner = self.auth_handle(c)
            if not owner: return self.send_json(401, {"error": "sign in with slack to create a project"})
            name = (d.get("name") or "").strip()
            pid = (d.get("id") or "").strip() or slugify(name)
            if not name: return self.send_json(400, {"error": "name required"})
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", pid):
                return self.send_json(400, {"error": "id must be lowercase letters, numbers, - and _"})
            repo_url = (d.get("repo_url") or "").strip() or None
            local_path = (d.get("local_path") or "").strip() or None
            try:
                c.execute(
                    "INSERT INTO project(id, owner, name, repo_url, head_from, head_to, local_path) VALUES (?,?,?,?,?,?,?)",
                    (pid, owner, name, repo_url, None, None, local_path),
                )
                c.commit()
            except sqlite3.IntegrityError: return self.send_json(409, {"error": "project exists"})
            return self.send_json(201, {
                "id": pid, "owner": owner, "name": name, "repo_url": repo_url,
                "repo_slug": repo_slug(repo_url), "archived": False, "deleted": False,
                "last_repo_url": None, "local_path": local_path,
            })
        if len(p) == 4 and p[:2] == ["v1", "projects"] and p[3] == "edit":
            d = self.body(); who = self.auth_handle(c)
            if not who: return self.send_json(401, {"error": "sign in with slack first"})
            row = c.execute("SELECT * FROM project WHERE id=?", (p[2],)).fetchone()
            if not row: return self.send_json(404, {"error": "project not found"})
            if row["owner"] != who: return self.send_json(403, {"error": "not your project"})
            name = (d.get("name") if d.get("name") is not None else row["name"])
            repo_url = d.get("repo_url") if "repo_url" in d else row["repo_url"]
            name = (name or "").strip()
            if not name: return self.send_json(400, {"error": "name required"})
            archived = 1 if d.get("archived") else 0 if "archived" in d else int(_urow(row, "archived", 0) or 0)
            last_repo = _urow(row, "last_repo_url")
            if "repo_url" in d and not (repo_url or "").strip() and row["repo_url"]:
                last_repo = row["repo_url"]
            if "repo_url" in d and (repo_url or "").strip():
                last_repo = None
            if "local_path" in d:
                local_path = (d.get("local_path") or "").strip() or None
            else:
                local_path = _urow(row, "local_path")
            c.execute(
                "UPDATE project SET name=?, repo_url=?, archived=?, last_repo_url=?, local_path=? WHERE id=?",
                (name, (repo_url or None), archived, last_repo, local_path, p[2]),
            )
            c.commit()
            return self.send_json(200, {
                "id": p[2], "owner": who, "name": name, "repo_url": repo_url or None,
                "repo_slug": repo_slug(repo_url), "archived": bool(archived),
                "deleted": bool(_urow(row, "deleted", 0)), "last_repo_url": last_repo,
                "local_path": local_path,
            })
        if len(p) == 4 and p[:2] == ["v1", "projects"] and p[3] == "archive":
            d = self.body(); who = self.auth_handle(c)
            if not who: return self.send_json(401, {"error": "sign in with slack first"})
            row = c.execute("SELECT * FROM project WHERE id=?", (p[2],)).fetchone()
            if not row: return self.send_json(404, {"error": "project not found"})
            if row["owner"] != who: return self.send_json(403, {"error": "not your project"})
            archived = 1 if d.get("archived", True) else 0
            c.execute("UPDATE project SET archived=? WHERE id=?", (archived, p[2])); c.commit()
            return self.send_json(200, {"id": p[2], "archived": bool(archived)})
        if len(p) == 4 and p[:2] == ["v1", "projects"] and p[3] == "delete":
            d = self.body(); who = self.auth_handle(c)
            if not who: return self.send_json(401, {"error": "sign in with slack first"})
            row = c.execute("SELECT * FROM project WHERE id=?", (p[2],)).fetchone()
            if not row: return self.send_json(404, {"error": "project not found"})
            if row["owner"] != who: return self.send_json(403, {"error": "not your project"})
            mode = (d.get("mode") or "repo").strip().lower()
            if mode == "project":
                confirm = (d.get("confirm") or "").strip()
                if confirm not in (p[2], row["name"] or ""):
                    return self.send_json(400, {"error": "type the project name or id to confirm"})
                c.execute("UPDATE project SET deleted=1, archived=1 WHERE id=?", (p[2],)); c.commit()
                return self.send_json(200, {"id": p[2], "deleted": True})
            if mode == "clear":
                c.execute("UPDATE project SET last_repo_url=NULL WHERE id=?", (p[2],)); c.commit()
                return self.send_json(200, {"id": p[2], "repo_url": row["repo_url"], "last_repo_url": None, "cleared": True})
            # default: delete/unlink repo from project
            last = row["repo_url"] or _urow(row, "last_repo_url")
            if not last and not row["repo_url"]:
                return self.send_json(400, {"error": "no repo linked"})
            c.execute(
                "UPDATE project SET repo_url=NULL, last_repo_url=? WHERE id=?",
                (last, p[2]),
            )
            c.commit()
            return self.send_json(200, {"id": p[2], "repo_url": None, "last_repo_url": last, "repo_deleted": True})
        if len(p) == 4 and p[:2] == ["v1", "projects"] and p[3] == "restore":
            who = self.auth_handle(c)
            if not who: return self.send_json(401, {"error": "sign in with slack first"})
            row = c.execute("SELECT * FROM project WHERE id=?", (p[2],)).fetchone()
            if not row: return self.send_json(404, {"error": "project not found"})
            if row["owner"] != who: return self.send_json(403, {"error": "not your project"})
            last = _urow(row, "last_repo_url")
            if last and not row["repo_url"]:
                c.execute(
                    "UPDATE project SET deleted=0, repo_url=?, last_repo_url=NULL WHERE id=?",
                    (last, p[2]),
                )
                c.commit()
                return self.send_json(200, {
                    "id": p[2], "deleted": False, "repo_url": last, "last_repo_url": None,
                    "repo_slug": repo_slug(last),
                })
            c.execute("UPDATE project SET deleted=0 WHERE id=?", (p[2],)); c.commit()
            return self.send_json(200, {"id": p[2], "deleted": False})
        if len(p) == 4 and p[:2] == ["v1", "projects"] and p[3] == "git":
            d = self.body()
            owner = self.auth_handle(c)
            row = c.execute("SELECT * FROM project WHERE id=?", (p[2],)).fetchone()
            if not row:
                if not owner: return self.send_json(401, {"error": "sign in with slack first"})
                c.execute(
                    "INSERT INTO project(id, owner, name, repo_url, head_from, head_to) VALUES (?,?,?,?,?,?)",
                    (p[2], owner, d.get("name") or p[2], d.get("repo_url"), d.get("head_from"), d.get("head_to")),
                )
            else:
                if owner and row["owner"] and row["owner"] != owner:
                    return self.send_json(403, {"error": "not your project"})
                c.execute(
                    "UPDATE project SET repo_url=?, head_from=?, head_to=?, last_repo_url=NULL WHERE id=?",
                    (d.get("repo_url"), d.get("head_from"), d.get("head_to"), p[2]),
                )
            c.commit(); return self.send_json(200, {"id": p[2], "repo_url": d.get("repo_url"), "repo_slug": repo_slug(d.get("repo_url")), "head_from": d.get("head_from"), "head_to": d.get("head_to")})
        if p == ["v1", "buckets", "batch"]:
            d = self.body(); buckets = d.get("buckets", [])
            if not isinstance(buckets, list) or not buckets: return self.send_json(400, {"error": "buckets required"})
            for i, b in enumerate(buckets):
                if not isinstance(b, dict) or not isinstance(b.get("counts"), dict) or not isinstance(b.get("git"), dict) \
                   or not all(k in b for k in ("bucket_id","project_id","prev_hash","hash","bucket_secs","bucket_start_wall","mono_delta_ms")):
                    return self.send_json(400, {"error": f"bucket {i}: missing required fields"})
            project = buckets[0]["project_id"]
            row = c.execute("SELECT last_hash FROM chain_head WHERE project_id=?", (project,)).fetchone()
            prev = row["last_hash"] if row else GENESIS
            for i, b in enumerate(buckets):
                errs = validate_allowlist(b)
                if errs: return self.send_json(400, {"error": f"bucket {i}: schema allowlist violation", "details": errs})
            seen = {r[0]: r[1] for r in c.execute("SELECT bucket_id, hash FROM bucket WHERE project_id=?", (project,))}
            for i, b in enumerate(buckets):
                if b["bucket_id"] in seen and bucket_hash(b) != seen[b["bucket_id"]]:
                    return self.send_json(422, {"error": f"bucket {i}: hash mismatch (tampered replay of known bucket_id)"})
            fresh = [b for b in buckets if b["bucket_id"] not in seen]
            if len(fresh) != len(buckets):
                return self.send_json(409, {"error": "duplicate bucket_id", "duplicates": len(buckets) - len(fresh)})
            for i, b in enumerate(fresh):
                if b["prev_hash"] != prev: return self.send_json(422, {"error": f"bucket {i}: prev_hash mismatch (chain broken)"})
                if bucket_hash(b) != b["hash"]: return self.send_json(422, {"error": f"bucket {i}: hash mismatch (tampered)"})
                prev = b["hash"]
            total = 0.0; user = self.auth_handle(c) or d.get("user_id")
            unsigned = 0
            for i, b in enumerate(fresh):
                iid = (b.get("install_id") or "").strip()
                pub_b64 = (b.get("install_pubkey") or "").strip()
                sig = b.get("signature")
                if not sig or not pub_b64 or not iid:
                    unsigned += 1
                    continue
                try:
                    pub_raw = install_sig._unb64(pub_b64)
                except Exception:
                    return self.send_json(422, {"error": f"bucket {i}: invalid install_pubkey"})
                if len(pub_raw) != 32:
                    return self.send_json(422, {"error": f"bucket {i}: install_pubkey must be 32 raw bytes"})
                expect_id = install_sig.install_id_from_pubkey(pub_raw)
                if iid != expect_id:
                    return self.send_json(422, {"error": f"bucket {i}: install_id does not match pubkey"})
                row = c.execute("SELECT pubkey FROM install_key WHERE install_id=?", (iid,)).fetchone()
                if row and row["pubkey"] != pub_b64:
                    return self.send_json(422, {"error": f"bucket {i}: install_pubkey mismatch for install_id"})
                if not row:
                    c.execute(
                        "INSERT INTO install_key VALUES (?,?,?,?)",
                        (iid, pub_b64, user, int(time.time())),
                    )
                if not install_sig.verify_signature(b["hash"], sig, pub_raw):
                    return self.send_json(422, {"error": f"bucket {i}: bad Ed25519 signature"})
            srow = c.execute("SELECT silent_secs FROM silent_streak WHERE project_id=?", (project,)).fetchone()
            seq = weight_sequence(fresh, srow["silent_secs"] if srow else 0)
            from verify import is_silent
            silent_end = (srow["silent_secs"] if srow else 0)
            for b in fresh: silent_end = silent_end + b["bucket_secs"] if is_silent(b) else 0
            c.execute("INSERT INTO silent_streak VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET silent_secs=excluded.silent_secs", (project, silent_end))
            for b, (w, state) in zip(fresh, seq):
                ws = billable_secs(b) * w; total += ws
                c.execute("INSERT INTO bucket VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (b["bucket_id"], user, project, b["bucket_start_wall"], b["bucket_secs"], state, w, ws, b["hash"], b["prev_hash"], json.dumps(b)))
            c.execute("INSERT INTO chain_head VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET last_hash=excluded.last_hash", (project, prev)); c.commit()
            return self.send_json(201, {
                "accepted": len(fresh),
                "weighted_secs_added": total,
                "weighted_hours_added": round(total / 3600, 4),
                "unsigned_buckets": unsigned,
            })
        if p == ["v1", "submissions"]:
            d = self.body()
            if not d.get("ai_declaration"): return self.send_json(400, {"error": "ai_declaration required"})
            rows = c.execute("SELECT * FROM bucket WHERE project_id=? AND bucket_id BETWEEN ? AND ? ORDER BY bucket_start_wall",
                             (d["project_id"], d["from_bucket"], d["to_bucket"])).fetchall()
            if not rows: return self.send_json(404, {"error": "no buckets in range"})
            claimed = sum(r["weighted_secs"] for r in rows)
            sid = str(uuid.uuid4()); fl = flags([json.loads(r["payload"]) for r in rows], claimed / 3600)
            proj = c.execute("SELECT * FROM project WHERE id=?", (d["project_id"],)).fetchone()
            if proj and not proj["repo_url"]: fl.append("git_mismatch: no repo linked")
            who = self.auth_handle(c) or d.get("user_id")
            c.execute("INSERT INTO submission VALUES (?,?,?,?,?,?,?)",
                      (sid, who, d["project_id"], claimed, d["ai_declaration"], "pending", json.dumps(fl)))
            notify(c, f"new submission: {d['project_id']} · {round(claimed/3600, 2)} weighted h by {who} · pending review")
            c.commit()
            ev = len(json.dumps([json.loads(r["payload"]) for r in rows]).encode())
            return self.send_json(201, {"id": sid, "claimed_weighted_secs": claimed, "claimed_weighted_hours": round(claimed / 3600, 4), "status": "pending", "flags": fl, "evidence_bytes": ev, "evidence_kb": round(ev / 1024, 1)})
        if len(p) == 4 and p[:2] == ["v1", "submissions"] and p[3] == "review":
            d = self.body()
            if d.get("reason_code") not in REASONS: return self.send_json(400, {"error": "invalid reason_code", "allowed": sorted(REASONS)})
            r = c.execute("SELECT * FROM submission WHERE id=?", (p[2],)).fetchone()
            if not r: return self.send_json(404, {"error": "submission not found"})
            approved = float(d["approved_weighted_secs"])
            status = "approved" if approved >= r["claimed_weighted_secs"] else ("rejected" if approved == 0 else "deflated")
            c.execute("UPDATE submission SET status=? WHERE id=?", (status, p[2]))
            c.execute("INSERT INTO review VALUES (?,?,?,?,?)", (str(uuid.uuid4()), p[2], d.get("reviewer_id"), approved, d["reason_code"]))
            notify(c, f"submission {p[2][:8]} reviewed: {status} · {round(approved/3600, 2)} h approved · reason {d['reason_code']}")
            c.commit()
            return self.send_json(200, {"id": p[2], "status": status, "approved_weighted_secs": approved})
        return self.send_json(404, {"error": "not found"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "18765"))
    # BIND=0.0.0.0 for public IP / port-forward; default loopback for local-only
    host = (os.environ.get("BIND") or os.environ.get("HOST") or "0.0.0.0").strip() or "0.0.0.0"
    print(f"listening on {host}:{port} db={DB}", flush=True)
    ThreadingHTTPServer((host, port), H).serve_forever()
