#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local debug logging — allowlisted labels only (never paths/prompts/args)."""
from __future__ import annotations

import json, os, sys

_TRUE = {"1", "true", "yes", "on"}
_SAFE_KEYS = frozenset({
    "editor", "agent", "event_type", "category", "verb", "op",
    "files_touched_count", "exit_code", "source", "project_id",
    "n", "kind", "msg",
})


def debug_enabled():
    v = (os.environ.get("STRUGGLETIME_DEBUG") or os.environ.get("AITIME_DEBUG") or "").strip().lower()
    return v in _TRUE


def debug_log(msg, **fields):
    """Print one stderr line when debug is on. Drops unknown / forbidden keys."""
    if not debug_enabled():
        return
    safe = {}
    for k, v in fields.items():
        if k not in _SAFE_KEYS:
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            safe[k] = v
    payload = {"msg": str(msg)[:80], **safe}
    print("[struggletime:debug] " + json.dumps(payload, separators=(",", ":")), file=sys.stderr, flush=True)
