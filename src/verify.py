#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reference chain verifier + causal weighting. Stdlib only.

Usage: python3 src/verify.py examples/session-closed-loop.json

Formula source of truth: this file. `weighting/rules-v0.4.0.yaml` and
`schemas/heartbeat.schema.json` are generated mirrors — do not edit them first.
"""
import hashlib, json, sys

GENESIS = "0" * 64
FORMULA_VERSION = "v0.4.0-alpha"

# Quiet follow-up after real work: keep partial credit for 5–12 minutes, then AFK=0.
SILENT_FULL_SECS = 300   # ≤5m still counts as silent debugging
SILENT_ZERO_SECS = 720   # ≥12m dead air → 0

ALLOWED_TOP = {
    "v","bucket_id","install_id","project_id","repo_remote_hash","bucket_start_wall","bucket_secs",
    "mono_delta_ms","counts","git","files","exec_categories","editors","agents","languages",
    "os","os_detail","formula_version","prev_hash","hash","signature","install_pubkey","sig_alg",
}
ALLOWED_COUNTS = {
    "human_edits","ai_writes","prompts","executions","exec_failures","commits","dwell_diff_secs",
    "idle_secs","active_secs","first_event_ts","last_event_ts",
    # Passive AI-struggle friction counters (IDE/agent agnostic)
    "same_file_reedit_count","ai_retry_loops","failed_exec_count",
    "test_fail_to_pass_transitions","agent_turn_churn","human_recovery_edits",
    # Zero-hook struggle tomography
    "atomic_agent_writes","git_plumbing_pulses","reflog_reverts",
    "build_test_cycles","cache_fail_to_pass",
}
ALLOWED_GIT = {"head_commit","lines_added","lines_deleted","dirty_files"}
# op/editor/agent/source/language are metadata labels only — never raw paths or content
ALLOWED_FILE = {"file_id","added","deleted","bursts","op","editor","agent","source","language"}
ALLOWED_FILE_OP = {"created","modified","deleted"}
ALLOWED_FILE_SOURCE = {"layer1","adapter"}
ALLOWED_OS_DETAIL = {"id","release","arch"}
FORBIDDEN = {"content","text","code","prompt_text","command","args","keystrokes","screenshot","image","path","filepath","file_path","raw","transcript"}

def anonymized_file_id(rel_path, salt):
    return hashlib.sha256(f"{salt}:{rel_path}".encode()).hexdigest()[:16]

def validate_allowlist(bucket):
    errs = []
    for label, obj, allowed in (("top", bucket, ALLOWED_TOP), ("counts", bucket.get("counts", {}), ALLOWED_COUNTS), ("git", bucket.get("git", {}), ALLOWED_GIT)):
        for k in obj:
            if k in FORBIDDEN: errs.append(f"{label}.{k}: forbidden (raw content can never be sent)")
            elif k not in allowed: errs.append(f"{label}.{k}: not in allowlist")
    if len(bucket.get("files", [])) > 50: errs.append("files: max 50 per bucket")
    for i, f in enumerate(bucket.get("files", [])):
        for k in f:
            if k in FORBIDDEN: errs.append(f"files[{i}].{k}: forbidden (raw content can never be sent)")
            elif k not in ALLOWED_FILE: errs.append(f"files[{i}].{k}: not in allowlist")
        if "file_id" in f and not all(ch in "0123456789abcdef" for ch in str(f["file_id"])):
            errs.append(f"files[{i}].file_id: must be anonymized hex id")
        if "op" in f and f["op"] not in ALLOWED_FILE_OP:
            errs.append(f"files[{i}].op: must be created|modified|deleted")
        if "source" in f and f["source"] not in ALLOWED_FILE_SOURCE:
            errs.append(f"files[{i}].source: must be layer1|adapter")
        for label in ("editor", "agent", "language"):
            if label in f and (not isinstance(f[label], str) or len(f[label]) > 32 or "/" in f[label] or " " in f[label]):
                errs.append(f"files[{i}].{label}: short metadata label only")
    for agg in ("editors", "agents", "languages"):
        obj = bucket.get(agg)
        if obj is None: continue
        if not isinstance(obj, dict):
            errs.append(f"{agg}: must be object of label→count")
            continue
        for k, v in obj.items():
            if not isinstance(k, str) or len(k) > 32 or "/" in k or " " in k:
                errs.append(f"{agg}.{k}: short metadata label only")
            if not isinstance(v, int) or v < 0:
                errs.append(f"{agg}.{k}: count must be non-negative int")
    if "os" in bucket:
        os_id = bucket["os"]
        if not isinstance(os_id, str) or not os_id or len(os_id) > 32 or "/" in os_id or " " in os_id:
            errs.append("os: short platform label only")
        if os_id == "untracked":
            errs.append("os: must not be untracked")
    if "os_detail" in bucket:
        detail = bucket["os_detail"]
        if not isinstance(detail, dict):
            errs.append("os_detail: must be object")
        else:
            for k in detail:
                if k not in ALLOWED_OS_DETAIL:
                    errs.append(f"os_detail.{k}: not in allowlist")
    return errs

def _has_activity(c):
    return any(c.get(k) for k in (
        "human_edits", "ai_writes", "prompts", "executions", "commits",
        "same_file_reedit_count", "atomic_agent_writes", "git_plumbing_pulses",
        "build_test_cycles", "cache_fail_to_pass", "test_fail_to_pass_transitions",
    ))

def is_silent(bucket):
    """No edit/exec/prompt/commit signals — quiet stretch (reading, thinking, AFK)."""
    return not _has_activity(bucket.get("counts") or {})

def decay_weight(silent_secs):
    """5–12 minute gradual decay for quiet follow-up; >12m → 0."""
    if silent_secs <= SILENT_FULL_SECS:
        return 0.70
    if silent_secs >= SILENT_ZERO_SECS:
        return 0.0
    return round(0.70 * (SILENT_ZERO_SECS - silent_secs) / (SILENT_ZERO_SECS - SILENT_FULL_SECS), 4)

def weight_sequence(buckets, initial_silent_secs=0):
    out, silent = [], initial_silent_secs
    for b in buckets:
        if is_silent(b):
            silent += b["bucket_secs"]
            out.append((decay_weight(silent), "SILENT_DEBUG"))
            continue
        silent = 0
        out.append(weight(b))
    return out

def billable_secs(bucket):
    """Active-span billing: short bursts credit their span; silent windows still use wall secs."""
    secs = int(bucket.get("bucket_secs") or 0)
    c = bucket.get("counts") or {}
    if is_silent(bucket):
        return max(0, secs)
    active = c.get("active_secs")
    if active is None:
        return max(0, secs)
    try:
        active = int(active)
    except (TypeError, ValueError):
        return max(0, secs)
    if active <= 0:
        return max(0, secs)
    return max(0, min(secs, active))

def friction(bucket):
    c = bucket.get("counts") or {}
    struggle = (
        0.12 * min(c.get("same_file_reedit_count", 0), 5) / 5
        + 0.12 * min(c.get("ai_retry_loops", 0), 4) / 4
        + 0.12 * min(int(c.get("test_fail_to_pass_transitions", 0) or 0) + int(c.get("cache_fail_to_pass", 0) or 0), 3) / 3
        + 0.08 * min(c.get("human_recovery_edits", 0), 4) / 4
        + 0.08 * min(c.get("agent_turn_churn", 0), 5) / 5
        + 0.08 * min(c.get("atomic_agent_writes", 0), 4) / 4
        + 0.08 * min(c.get("git_plumbing_pulses", 0), 4) / 4
        + 0.08 * min(c.get("build_test_cycles", 0), 4) / 4
    )
    raw = (
        0.25 * min(c.get("exec_failures", 0) or c.get("failed_exec_count", 0), 3) / 3
        + 0.15 * min(c.get("executions", 0), 4) / 4
        + 0.10 * min(c.get("human_edits", 0), 10) / 10
        + 0.10 * (1 if (c.get("commits") or c.get("human_edits")) else 0) * min(c.get("prompts", 0) + 1, 3) / 3
        + struggle
    )
    return round(min(raw, 1.0), 3)

def friction_timeline(buckets):
    seq = weight_sequence(buckets)
    out = []
    for i, b in enumerate(buckets):
        c = b.get("counts") or {}
        g = b.get("git") or {}
        out.append({
            "bucket_id": b.get("bucket_id"),
            "start": b.get("bucket_start_wall"),
            "state": seq[i][1],
            "weight": seq[i][0],
            "friction": friction(b) if c else 0.0,
            "head_commit": g.get("head_commit"),
            "commits": int(c.get("commits") or 0),
            "exec_failures": int(c.get("exec_failures") or 0),
            "active_secs": int(c.get("active_secs") or 0),
            "idle_secs": int(c.get("idle_secs") or 0),
            "reedits": int(c.get("same_file_reedit_count") or 0),
            "fail_to_pass": int(c.get("test_fail_to_pass_transitions") or 0) + int(c.get("cache_fail_to_pass") or 0),
            "git_pulses": int(c.get("git_plumbing_pulses") or 0),
            "build_cycles": int(c.get("build_test_cycles") or 0),
            "recovery_edits": int(c.get("human_recovery_edits") or 0),
            "files": [
                {"file_id": f.get("file_id"), "op": f.get("op"), "language": f.get("language")}
                for f in (b.get("files") or [])[:12]
                if isinstance(f, dict) and f.get("file_id")
            ],
        })
    return out

def canonical(bucket):
    b = {k: v for k, v in bucket.items() if k not in ("hash", "signature")}
    return json.dumps(b, sort_keys=True, separators=(",", ":")).encode()

def bucket_hash(bucket):
    return hashlib.sha256(canonical(bucket) + bucket["prev_hash"].encode()).hexdigest()

def verify_chain(buckets):
    errors, prev, last_mono, last_wall = [], GENESIS, None, None
    for i, b in enumerate(buckets):
        if b.get("prev_hash") != prev:
            errors.append(f"bucket {i}: prev_hash mismatch (chain broken)")
        try:
            if bucket_hash(b) != b.get("hash"):
                errors.append(f"bucket {i}: hash mismatch (tampered)")
        except Exception:
            errors.append(f"bucket {i}: hash mismatch (tampered)")
        mono = b.get("mono_delta_ms")
        if last_mono is not None and mono is not None and mono < 0:
            errors.append(f"bucket {i}: monotonic clock regression")
        wall = b.get("bucket_start_wall")
        if last_wall is not None and wall is not None:
            jump = int(wall) - int(last_wall)
            if jump < -60:
                errors.append(f"bucket {i}: wall clock jumped backward ({jump}s)")
            elif jump > 6 * 3600:
                errors.append(f"bucket {i}: wall clock jumped forward ({jump}s)")
        prev = b.get("hash") or prev
        last_mono = mono if mono is not None else last_mono
        last_wall = wall if wall is not None else last_wall
    return errors


def anomaly_pairs(buckets, wall_jump_secs=3600, burst_files=20):
    """Side-by-side normal vs anomalous adjacent buckets (metadata only)."""
    pairs, prev = [], None
    chain_errs = set()
    for err in verify_chain(buckets):
        m = err.split(":")[0]
        if m.startswith("bucket "):
            try:
                chain_errs.add(int(m.split()[1]))
            except ValueError:
                pass
    for i, b in enumerate(buckets):
        c = b.get("counts") or {}
        reasons = []
        if i in chain_errs:
            reasons.append("chain_break")
        if prev is not None:
            jump = int(b.get("bucket_start_wall") or 0) - int(prev.get("bucket_start_wall") or 0)
            if abs(jump) > wall_jump_secs:
                reasons.append("clock_jump")
        files = b.get("files") or []
        if len(files) >= burst_files and int(c.get("human_edits") or 0) == 0 and int(c.get("executions") or 0) == 0:
            reasons.append("burst_dump")
        if int(c.get("ai_writes") or 0) > 5 * max(1, int(c.get("human_edits") or 0)) and int(c.get("executions") or 0) == 0:
            reasons.append("ai_ratio")
        if not b.get("signature"):
            reasons.append("unsigned")
        if reasons:
            pairs.append({
                "index": i,
                "reasons": reasons,
                "bucket": _bucket_chip(b),
                "neighbor": _bucket_chip(prev) if prev is not None else None,
            })
        prev = b
    return pairs


def _bucket_chip(b):
    if not b:
        return None
    c = b.get("counts") or {}
    return {
        "bucket_id": (b.get("bucket_id") or "")[:12],
        "start": b.get("bucket_start_wall"),
        "secs": b.get("bucket_secs"),
        "prev_hash": (b.get("prev_hash") or "")[:12],
        "hash": (b.get("hash") or "")[:12],
        "active_secs": int(c.get("active_secs") or 0),
        "idle_secs": int(c.get("idle_secs") or 0),
        "ai_writes": int(c.get("ai_writes") or 0),
        "human_edits": int(c.get("human_edits") or 0),
        "commits": int(c.get("commits") or 0),
        "files": len(b.get("files") or []),
        "signed": bool(b.get("signature")),
    }


def struggle_to_ship(buckets):
    """Weighted productive-struggle hours vs commit/ship anchors."""
    struggle_states = {"AI_STRUGGLE", "STRUGGLE_LOOP", "CLOSED_LOOP"}
    struggle_secs = 0.0
    commits = 0
    for b, (w, state) in zip(buckets, weight_sequence(buckets)):
        c = b.get("counts") or {}
        commits += int(c.get("commits") or 0)
        if state in struggle_states or state.startswith("AI_STRUGGLE") or state.startswith("STRUGGLE_LOOP"):
            struggle_secs += billable_secs(b) * w
    struggle_h = round(struggle_secs / 3600, 4)
    ship = max(commits, 0)
    ratio = round(struggle_h / ship, 4) if ship else None
    return {
        "struggle_hours": struggle_h,
        "ship_units": ship,
        "commits": commits,
        "velocity_ratio": ratio,
        "label": "struggle_hours_per_commit" if ship else "no_ship_anchors",
    }

def _idle_decay_factor(idle_secs):
    """Within-bucket quiet after last event: gradual 5–12m curve, not a 240s cliff."""
    idle = int(idle_secs or 0)
    if idle <= SILENT_FULL_SECS:
        return 1.0
    if idle >= SILENT_ZERO_SECS:
        return 0.0
    return round((SILENT_ZERO_SECS - idle) / (SILENT_ZERO_SECS - SILENT_FULL_SECS), 4)

def is_ai_struggle(c):
    """Iterative recovery from event sequences + zero-hook tomography — not a bulk dump."""
    reedit = int(c.get("same_file_reedit_count") or 0)
    retries = int(c.get("ai_retry_loops") or 0)
    fail_pass = int(c.get("test_fail_to_pass_transitions") or 0) + int(c.get("cache_fail_to_pass") or 0)
    churn = int(c.get("agent_turn_churn") or 0)
    recovery = int(c.get("human_recovery_edits") or 0)
    failed = int(c.get("failed_exec_count") or 0) or int(c.get("exec_failures") or 0)
    ai = int(c.get("ai_writes") or 0)
    prompts = int(c.get("prompts") or 0)
    human = int(c.get("human_edits") or 0)
    atomic = int(c.get("atomic_agent_writes") or 0)
    git_pulses = int(c.get("git_plumbing_pulses") or 0)
    build_cycles = int(c.get("build_test_cycles") or 0)
    reverts = int(c.get("reflog_reverts") or 0)

    if fail_pass > 0:
        return True
    if failed > 0 and reedit > 0 and (ai > 0 or churn > 0 or prompts > 0 or human > 0 or recovery > 0 or atomic > 0):
        return True
    if failed > 0 and (retries > 0 or recovery > 0) and (reedit > 0 or human > 0 or ai > 0):
        return True
    if retries >= 2 and (failed > 0 or recovery > 0 or fail_pass > 0):
        return True
    if churn >= 3 and failed > 0 and (reedit > 0 or recovery > 0 or human > 0):
        return True
    if recovery > 0 and (ai > 0 or failed > 0) and (reedit > 0 or fail_pass > 0 or retries > 0):
        return True
    # Zero-hook tomography: re-edits + build/test cache or git plumbing thrash
    if reedit >= 2 and (build_cycles >= 1 or git_pulses >= 2 or atomic >= 1):
        return True
    if build_cycles >= 2 and (reedit >= 1 or atomic >= 1 or human >= 1 or ai >= 1):
        return True
    if atomic >= 2 and reedit >= 2:
        return True
    if git_pulses >= 3 and (reedit >= 1 or reverts >= 1 or build_cycles >= 1):
        return True
    if reedit >= 2 and (retries >= 1 or recovery >= 1 or ai > 0 or human >= 2):
        return True
    if reedit >= 3 and (ai > 0 or prompts > 0 or churn > 0 or failed > 0 or atomic > 0):
        return True
    return False

def weight(bucket):
    """Causal weight from fused passive signals — not raw file-count heuristics."""
    c = bucket.get("counts") or {}
    cats = bucket.get("exec_categories") or {}
    followup = c.get("human_edits", 0) > 0 or c.get("commits", 0) > 0 or c.get("human_recovery_edits", 0) > 0
    steering = c.get("prompts", 0) > 0 or c.get("human_edits", 0) > 0 or c.get("agent_turn_churn", 0) > 0
    verified_loop = (
        c.get("exec_failures", 0) > 0
        and c.get("executions", 0) > 1
        and followup
    )
    closed = steering and c.get("executions", 0) > 0 and followup
    formatterish = int(cats.get("formatter", 0) or 0) > 0
    ai_struggle = is_ai_struggle(c)

    # Unverified bulk AI: multi-file agent dump with no test/build/edit follow-up / no struggle
    open_loop = (
        c.get("ai_writes", 0) > 0
        and c.get("human_edits", 0) == 0
        and c.get("human_recovery_edits", 0) == 0
        and c.get("executions", 0) == 0
        and c.get("dwell_diff_secs", 0) < 30
        and not formatterish
        and not ai_struggle
        and c.get("same_file_reedit_count", 0) == 0
        and c.get("ai_retry_loops", 0) == 0
        and c.get("git_plumbing_pulses", 0) == 0
        and c.get("build_test_cycles", 0) == 0
        and c.get("atomic_agent_writes", 0) == 0
        and c.get("cache_fail_to_pass", 0) == 0
    )
    # Passive AI-generation staring: AI writes then long idle, no recovery
    ai_stare = (
        c.get("ai_writes", 0) > 0
        and c.get("human_edits", 0) == 0
        and c.get("executions", 0) == 0
        and c.get("idle_secs", 0) >= SILENT_FULL_SECS
        and not ai_struggle
    )

    if ai_struggle or (verified_loop and (c.get("ai_writes", 0) > 0 or c.get("prompts", 0) > 0 or c.get("agent_turn_churn", 0) > 0)):
        w, state = 1.0, "AI_STRUGGLE"
    elif verified_loop:
        w, state = 1.0, "STRUGGLE_LOOP"
    elif closed:
        w, state = 1.0, "CLOSED_LOOP"
    elif c.get("ai_writes", 0) > 0 and c.get("dwell_diff_secs", 0) >= 30 and followup:
        w, state = 0.85, "AI_REVIEWED"
    elif open_loop or ai_stare:
        w, state = 0.20, "OPEN_LOOP_AGENT_DUMP"
    elif c.get("human_edits", 0) > 0:
        w, state = 0.70, "HUMAN_UNVERIFIED"
    elif c.get("ai_writes", 0) > 0:
        w, state = 0.25, "AI_UNREVIEWED"
    elif c.get("executions", 0) > 0:
        w, state = 0.50, "EXEC_ONLY"
    elif is_silent(bucket):
        w, state = 0.70, "SILENT_DEBUG"
    else:
        w, state = 0.0, "FALLBACK"

    # Gradual idle decay after the active span (replaces idle_secs >= 240 → 0 cliff)
    # Do not decay AI_STRUGGLE / STRUGGLE_LOOP — recovery work already paid the cost
    if state not in ("SILENT_DEBUG", "FALLBACK", "AI_STRUGGLE", "STRUGGLE_LOOP") and c.get("idle_secs", 0) > SILENT_FULL_SECS:
        factor = _idle_decay_factor(c.get("idle_secs", 0))
        w = round(w * factor, 4)
        if factor <= 0:
            state = "IDLE"
        elif factor < 1.0:
            state = state + "_DECAY"
    return w, state

def flags(buckets, weighted_hours):
    total = lambda k: sum((b.get("counts") or {}).get(k, 0) for b in buckets)
    lines = sum(
        int((b.get("git") or {}).get("lines_added") or 0) + int((b.get("git") or {}).get("lines_deleted") or 0)
        for b in buckets
    )
    out = []
    if lines and weighted_hours / (lines / 1000) > 10:
        out.append(f"hours_per_kloc={weighted_hours/(lines/1000):.1f} (>10)")
    if total("executions") >= 5 and total("exec_failures") == 0 and total("commits") == 0:
        out.append("exec_success_only: >=5 executions, 0 failures, 0 commits")
    if total("ai_writes") / max(1, total("human_edits")) > 5:
        out.append(f"ai_human_ratio={total('ai_writes')/max(1,total('human_edits')):.1f} (>5)")
    return out

def score(buckets):
    rows, weighted = [], 0.0
    for b, (w, state) in zip(buckets, weight_sequence(buckets)):
        weighted += billable_secs(b) * w
        bid = str(b.get("bucket_id") or "")[:8]
        rows.append({"bucket": bid, "state": state, "weight": w, "friction": friction(b) if b.get("counts") else 0.0})
    return rows, weighted / 3600

def main(path):
    buckets = json.load(open(path))
    errors = verify_chain(buckets)
    rows, hours = score(buckets)
    print(json.dumps({"chain_errors": errors, "buckets": rows,
                      "weighted_hours": round(hours, 3),
                      "flags": flags(buckets, hours)}, indent=2))
    return 1 if errors else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
