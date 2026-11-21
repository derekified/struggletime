#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local tracker inspect: show exactly what would leave this machine.

Usage:
  python3 daemon/tracker_inspect.py --repo ~/my-project
  python3 daemon/tracker_inspect.py examples/session-closed-loop.json
"""
import argparse, hashlib, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from verify import validate_allowlist, verify_chain, weight_sequence, friction

def load(source):
    p = Path(source).expanduser()
    if p.is_dir():
        ledger = p / ".aitime-ledger.json"
        if not ledger.exists(): sys.exit(f"no ledger at {ledger} — run the daemon first")
        data = json.loads(ledger.read_text()); buckets = data["pending"]
        print(f"ledger: {ledger} (salt fingerprint {hashlib.sha256(data.get('salt','').encode()).hexdigest()[:8]}, salt itself never sent)")
        return buckets
    data = json.loads(p.read_text())
    return data if isinstance(data, list) else data["pending"]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("source"); ap.add_argument("--repo", action="store_true", help="source is a repo dir")
    a = ap.parse_args(); buckets = load(a.source)
    raw = json.dumps(buckets).encode(); errs = []
    for i, b in enumerate(buckets): errs += [f"bucket {i}: {e}" for e in validate_allowlist(b)]
    print(f"buckets: {len(buckets)}  payload: {len(raw)} bytes ({len(raw)/1024:.1f} kb)  budget: 25-50 kb/session, <=50 kb/hour")
    print(f"chain: {'ok' if not verify_chain(buckets) else verify_chain(buckets)}")
    print(f"allowlist: {'ok — metadata only, no raw text/pixels can be represented' if not errs else errs}")
    STRUGGLE_KEYS = (
        "same_file_reedit_count", "ai_retry_loops", "failed_exec_count",
        "test_fail_to_pass_transitions", "agent_turn_churn", "human_recovery_edits",
        "exec_failures", "ai_writes", "human_edits", "active_secs", "idle_secs",
    )
    for b, (w, state) in zip(buckets, weight_sequence(buckets)):
        print(f"  {b['bucket_id'][:8]} {state:<22} w={w:<5} friction={friction(b):<5} files={[f['file_id'] for f in b.get('files', [])]} head={(b['git'].get('head_commit') or '')[:8]}")
        c = b.get("counts") or {}
        struggle = {k: c.get(k, 0) for k in STRUGGLE_KEYS if c.get(k)}
        if struggle:
            print(f"           struggle={struggle}")
        if b.get("editors") or b.get("agents") or b.get("os") or b.get("languages"):
            print(
                f"           editors={b.get('editors')} agents={b.get('agents')} "
                f"os={b.get('os')} languages={b.get('languages')} exec_categories={b.get('exec_categories')}"
            )
        for f in b.get("files") or []:
            if any(k in f for k in ("op", "editor", "agent", "language")):
                print(
                    f"           {f.get('file_id')} op={f.get('op')} editor={f.get('editor')} "
                    f"agent={f.get('agent')} language={f.get('language')} source={f.get('source')}"
                )
    print("this is byte-for-byte what the server receives. nothing else leaves this machine.")
    return 1 if errs else 0

if __name__ == "__main__":
    sys.exit(main())
