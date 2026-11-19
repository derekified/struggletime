#!/usr/bin/env python3
"""Reference chain verifier + causal weighting. Stdlib only.

Usage: python3 src/verify.py examples/session-closed-loop.json
"""
import hashlib, json, sys

GENESIS = "0" * 64

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
    for b in buckets:
        w, state = weight(b)
        weighted += b["bucket_secs"] * w
        rows.append({"bucket": b["bucket_id"][:8], "state": state, "weight": w})
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
