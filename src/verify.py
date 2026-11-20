#!/usr/bin/env python3
"""Reference chain verifier + causal weighting. Stdlib only.

Usage: python3 src/verify.py examples/session-closed-loop.json
"""
import hashlib, json, sys

GENESIS = "0" * 64

ALLOWED_TOP = {
    "v","bucket_id","install_id","project_id","repo_remote_hash","bucket_start_wall","bucket_secs",
    "mono_delta_ms","counts","git","files","exec_categories","editors","agents","languages",
    "os","os_detail","formula_version","prev_hash","hash","signature",
}
ALLOWED_COUNTS = {"human_edits","ai_writes","prompts","executions","exec_failures","commits","dwell_diff_secs","idle_secs"}
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

def is_silent(bucket):
    c = bucket["counts"]
    return not any(c[k] for k in ("human_edits","ai_writes","prompts","executions","commits")) and c["idle_secs"] < 240

def decay_weight(silent_secs):
    if silent_secs <= 300: return 0.70
    if silent_secs >= 720: return 0.0
    return round(0.70 * (720 - silent_secs) / 420, 4)

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

def friction(bucket):
    c = bucket["counts"]
    raw = 0.35 * min(c["exec_failures"], 3) / 3 + 0.25 * min(c["executions"], 4) / 4 \
        + 0.20 * min(c["human_edits"], 10) / 10 + 0.20 * (1 if (c["commits"] or c["human_edits"]) else 0) * min(c["prompts"] + 1, 3) / 3
    return round(min(raw, 1.0), 3)

def friction_timeline(buckets):
    seq = weight_sequence(buckets)
    return [{"bucket_id": b["bucket_id"], "start": b["bucket_start_wall"], "state": seq[i][1],
             "weight": seq[i][0], "friction": friction(b), "head_commit": b["git"].get("head_commit"),
             "commits": b["counts"]["commits"], "exec_failures": b["counts"]["exec_failures"],
             "files": b.get("files", [])} for i, b in enumerate(buckets)]

def canonical(bucket):
    b = {k: v for k, v in bucket.items() if k not in ("hash", "signature")}
    return json.dumps(b, sort_keys=True, separators=(",", ":")).encode()

def bucket_hash(bucket):
    return hashlib.sha256(canonical(bucket) + bucket["prev_hash"].encode()).hexdigest()

def verify_chain(buckets):
    errors, prev, last_mono = [], GENESIS, None
    for i, b in enumerate(buckets):
        if b["prev_hash"] != prev:
            errors.append(f"bucket {i}: prev_hash mismatch (chain broken)")
        if bucket_hash(b) != b["hash"]:
            errors.append(f"bucket {i}: hash mismatch (tampered)")
        if last_mono is not None and b["mono_delta_ms"] < 0:
            errors.append(f"bucket {i}: monotonic clock regression")
        prev, last_mono = b["hash"], b["mono_delta_ms"]
    return errors

def weight(bucket):
    c, secs = bucket["counts"], bucket["bucket_secs"]
    followup = c["human_edits"] > 0 or c["commits"] > 0
    steering = c["prompts"] > 0 or c["human_edits"] > 0
    if c["idle_secs"] >= 240:
        return 0.0, "IDLE"
    if c["ai_writes"] > 0 and c["human_edits"] == 0 and c["executions"] == 0 and c["dwell_diff_secs"] < 30:
        return 0.20, "OPEN_LOOP_AGENT_DUMP"
    if c["exec_failures"] > 0 and c["executions"] > 1 and followup:
        return 1.0, "STRUGGLE_LOOP"
    if steering and c["executions"] > 0 and followup:
        return 1.0, "CLOSED_LOOP"
    if c["ai_writes"] > 0 and c["dwell_diff_secs"] >= 30 and followup:
        return 0.85, "AI_REVIEWED"
    if c["human_edits"] > 0:
        return 0.70, "HUMAN_UNVERIFIED"
    if c["ai_writes"] > 0:
        return 0.25, "AI_UNREVIEWED"
    if c["executions"] > 0:
        return 0.50, "EXEC_ONLY"
    if is_silent(bucket):
        return 0.70, "SILENT_DEBUG"
    return 0.0, "FALLBACK"

def flags(buckets, weighted_hours):
    total = lambda k: sum(b["counts"][k] for b in buckets)
    lines = sum(b["git"]["lines_added"] + b["git"]["lines_deleted"] for b in buckets)
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
        weighted += b["bucket_secs"] * w
        rows.append({"bucket": b["bucket_id"][:8], "state": state, "weight": w, "friction": friction(b)})
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
