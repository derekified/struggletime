#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared project link / create flow for tray + settings GUI.

Projects dropdown + optional GitHub repo (None allowed) + local directory,
persisted to the API and ~/.aitime/gui.json so daemon.py tracks --repo/--project.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path = [p for p in sys.path if Path(p or ".").resolve() != _HERE]
sys.path.insert(0, str(_HERE))

import browser_auth
import config as dcfg
import native_ui


NONE_REPO = {"id": "", "label": "None — no GitHub repo", "repo_url": None}


def list_projects(server, token):
    code, data = browser_auth.api_json(server, "GET", "/v1/projects", token=token)
    if code != 200:
        err = (data.get("error") if isinstance(data, dict) else None) or f"HTTP {code}"
        return [], err
    out = []
    for p in data.get("projects") or []:
        if p.get("deleted"):
            continue
        if p.get("archived"):
            continue
        pid = p.get("id") or ""
        if not pid:
            continue
        name = p.get("name") or pid
        label = name if name == pid else f"{name} ({pid})"
        out.append({
            "id": pid,
            "name": name,
            "label": label,
            "repo_url": p.get("repo_url"),
            "local_path": p.get("local_path") or "",
        })
    out.sort(key=lambda x: (x["label"] or "").lower())
    return out, None


def list_github_repos(server, token):
    """Return (options, error_or_None). Always includes a None option first."""
    opts = [dict(NONE_REPO)]
    code, data = browser_auth.api_json(server, "GET", "/v1/me/github-repos", token=token)
    if code == 200:
        for r in data.get("repos") or []:
            url = r.get("repo_url") or ""
            if not url:
                continue
            name = r.get("full_name") or r.get("name") or url
            priv = " (private)" if r.get("private") else ""
            used = " · already linked" if r.get("already_added") else ""
            opts.append({
                "id": url,
                "label": f"{name}{priv}{used}",
                "repo_url": url,
            })
        return opts, None
    if code in (400, 401):
        msg = (data.get("error") if isinstance(data, dict) else None) or "GitHub not connected"
        return opts, msg
    err = (data.get("error") if isinstance(data, dict) else None) or f"GitHub repos failed ({code})"
    return opts, err


def save_local_mapping(settings, *, project_id, local_path, repo_url=None, path=None):
    """Write project + directory into ~/.aitime/gui.json (and optional handle/server)."""
    s = dict(settings or {})
    s["server"] = dcfg.normalize_server(s.get("server"))
    if project_id:
        s["project"] = project_id
    if local_path:
        s["repo"] = str(Path(local_path).expanduser())
    dcfg.save_settings(s, path)
    return s


def upsert_project(server, token, *, project_id=None, name=None, repo_url=None, local_path=None, create=False):
    """Create or edit a project. Explicit repo_url=None means no GitHub link. local_path optional."""
    body = {"repo_url": repo_url or None}
    if name is not None:
        body["name"] = name
    if local_path is not None:
        body["local_path"] = local_path or None

    if create or not project_id:
        nm = (name or project_id or "").strip()
        if not nm:
            return False, None, "project name required"
        payload = {"name": nm, "repo_url": repo_url or None}
        if project_id:
            payload["id"] = project_id
        if local_path:
            payload["local_path"] = local_path
        code, data = browser_auth.api_json(server, "POST", "/v1/projects", token=token, body=payload)
        if code == 409 and project_id:
            code, data = browser_auth.api_json(
                server, "POST", f"/v1/projects/{project_id}/edit",
                token=token, body=body,
            )
        if code not in (200, 201):
            err = (data.get("error") if isinstance(data, dict) else None) or f"failed ({code})"
            return False, None, err
        return True, data, None

    code, data = browser_auth.api_json(
        server, "POST", f"/v1/projects/{project_id}/edit",
        token=token, body=body,
    )
    if code != 200:
        err = (data.get("error") if isinstance(data, dict) else None) or f"failed ({code})"
        return False, None, err
    return True, data, None


def interactive_link(settings, *, prefer_create=False):
    """Native wizard: pick/create project, optional GitHub, choose directory. Returns (settings, message)."""
    s = dict(settings or {})
    s["server"] = dcfg.normalize_server(s.get("server"))
    token = s.get("token") or ""
    if not token:
        native_ui.alert("StruggleTime", "Set your StruggleTime API key first.")
        return s, "no API key"

    projects, perr = list_projects(s["server"], token)
    if perr:
        native_ui.alert("StruggleTime", f"Could not load projects: {perr}")
        return s, perr

    create = prefer_create
    project_id = None
    name = None

    if not create:
        labels = ["+ Create new project"] + [p["label"] for p in projects]
        pick = native_ui.choose("Link project", "Select a project to link, or create a new one:", labels)
        if pick is None:
            return s, "cancelled"
        if pick == 0:
            create = True
        else:
            chosen = projects[pick - 1]
            project_id = chosen["id"]
            name = chosen["name"]

    if create:
        name = native_ui.prompt("New project", "Project name:", default=Path(s.get("repo") or "").name or "")
        if name is None:
            return s, "cancelled"
        name = name.strip()
        if not name:
            native_ui.alert("StruggleTime", "Name required.")
            return s, "no name"

    gh_opts, gh_note = list_github_repos(s["server"], token)
    gh_labels = [o["label"] for o in gh_opts]
    hint = "GitHub is optional — pick None to skip."
    if gh_note:
        hint = f"{hint}\n({gh_note})"
    # Preselect existing repo if editing
    default_gh = 0
    if project_id:
        cur = next((p for p in projects if p["id"] == project_id), None)
        if cur and cur.get("repo_url"):
            for i, o in enumerate(gh_opts):
                if o.get("repo_url") == cur["repo_url"]:
                    default_gh = i
                    break
    gh_pick = native_ui.choose("GitHub repo (optional)", hint, gh_labels, default=default_gh)
    if gh_pick is None:
        return s, "cancelled"
    repo_url = gh_opts[gh_pick].get("repo_url") if 0 <= gh_pick < len(gh_opts) else None

    start = s.get("repo") or ""
    if not start and project_id:
        cur = next((p for p in projects if p["id"] == project_id), None)
        if cur and cur.get("local_path"):
            start = cur["local_path"]
    path = native_ui.choose_folder("Local directory to track", start or str(Path.home()))
    if not path:
        return s, "cancelled"
    path = str(Path(path).expanduser())
    if not Path(path).is_dir():
        native_ui.alert("StruggleTime", "That folder does not exist.")
        return s, "bad directory"

    ok, data, err = upsert_project(
        s["server"], token,
        project_id=project_id,
        name=name or project_id,
        repo_url=repo_url,
        local_path=path,
        create=create,
    )
    if not ok:
        native_ui.alert("StruggleTime", err or "link failed")
        return s, err or "link failed"

    pid = (data or {}).get("id") or project_id
    s = save_local_mapping(s, project_id=pid, local_path=path, repo_url=repo_url)
    gh = repo_url or "no GitHub repo"
    msg = f"linked {pid} → {path} · {gh}"
    native_ui.alert("StruggleTime", msg)
    return s, msg
