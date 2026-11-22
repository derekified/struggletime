#!/usr/bin/env bash
# Isolated adversarial subagent harness for StruggleTime.
# Uses TMPDIR + dummy API key only. Never touches real user data.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${STRUGGLETIME_BIN:-$ROOT/rust/target/release/struggletime}"
PORT="${HARNESS_PORT:-0}"
REAL_HOME="${HOME:-/root}"
export RUSTUP_HOME="${RUSTUP_HOME:-$REAL_HOME/.rustup}"
export CARGO_HOME="${CARGO_HOME:-$REAL_HOME/.cargo}"
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/st-adversarial.XXXXXX")"
cleanup() {
  if [[ -n "${SERVER_PID:-}" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

export STRUGGLETIME_HOME="$WORKDIR/aitime"
export STRUGGLETIME_CONFIG="$WORKDIR/config"
export HOME="$WORKDIR/home"
mkdir -p "$STRUGGLETIME_HOME" "$STRUGGLETIME_CONFIG" "$HOME" \
  "$WORKDIR/repo-a/src" "$WORKDIR/repo-b/src" "$WORKDIR/db"

echo "== harness workdir: $WORKDIR"

if [[ ! -x "$BIN" ]]; then
  echo "== building struggletime"
  (cd "$ROOT/rust" && cargo build --release -p struggletime)
fi

# Pick a free local port unless HARNESS_PORT is set.
if [[ "$PORT" = "0" ]]; then
  PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
fi

DB="$WORKDIR/db/server.db"
"$BIN" serve --bind 127.0.0.1 --port "$PORT" --db "$DB" \
  --static-dir "$ROOT/server/static" >"$WORKDIR/server.log" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 80); do
  if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "server died during startup"; cat "$WORKDIR/server.log"; exit 1
  fi
  sleep 0.1
done
curl -sf "http://127.0.0.1:$PORT/health" >/dev/null

KEY_JSON="$(curl -sf -X POST "http://127.0.0.1:$PORT/v1/keys" \
  -H 'Content-Type: application/json' \
  -d '{"handle":"harness-bot"}' || true)"
TOKEN="$(printf '%s' "$KEY_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')"
case "$TOKEN" in
  st_*) ;;
  *) echo "bad dummy token from: $KEY_JSON"; cat "$WORKDIR/server.log"; exit 1 ;;
esac
echo "== port $PORT dummy key prefix: ${TOKEN:0:8}…"

SERVER="http://127.0.0.1:$PORT"
assert_no_leaks() {
  local file="$1"
  if grep -Eiq 'prompt_text|/Users/|/home/[^/]+/|rm -rf|transcript|argv|BEGIN RSA' "$file"; then
    echo "PRIVACY LEAK in $file"
    grep -Ein 'prompt_text|/Users/|/home/|rm -rf|transcript|argv' "$file" | head
    exit 1
  fi
  # Unhashed absolute paths must not appear as JSON string values.
  if python3 - "$file" <<'PY'
import json,sys,re
raw=open(sys.argv[1]).read()
# ledger is JSON; also accept JSONL
try:
    data=json.loads(raw)
except Exception:
    sys.exit(0)
def walk(o, path=""):
    if isinstance(o, dict):
        for k,v in o.items():
            if k in ("file_id","hash","prev_hash","salt","bucket_id","install_id","project_id","formula_version","os","op","source","editor","agent","language","state"):
                walk(v, path+"."+k); continue
            if isinstance(v,str) and (v.startswith("/") or re.search(r"[A-Za-z]:\\\\", v)):
                print(f"unhashed path-like value at {path}.{k}: {v[:80]}")
                sys.exit(2)
            walk(v, path+"."+k)
    elif isinstance(o, list):
        for i,v in enumerate(o):
            walk(v, f"{path}[{i}]")
walk(data)
sys.exit(0)
PY
  then
    :
  else
    echo "PRIVACY LEAK (path-like) in $file"; exit 1
  fi
}

write_events() {
  local dest="$STRUGGLETIME_HOME/agent_events.jsonl"
  cat >"$dest"
}

run_once() {
  local repo="$1" project="$2"
  "$BIN" daemon --once --bucket-secs 30 \
    --repo "$repo" --project "$project" \
    --server "$SERVER" --token "$TOKEN" \
    --user harness-bot >>"$WORKDIR/daemon.log" 2>&1 || true
}

echo
echo "== scenario 1: parallel fan-out (two projects, concurrent tool/edit events)"
printf 'fn a() {}\n' >"$WORKDIR/repo-a/src/a.rs"
printf 'fn b() {}\n' >"$WORKDIR/repo-b/src/b.rs"
write_events <<'EOF'
{"event_type":"edit","project_id":"proj-a","editor":"cursor","files_touched_count":1}
{"event_type":"tool","category":"ai_cli","project_id":"proj-b","files_touched_count":3}
{"event_type":"tool","category":"ai_cli","project_id":"proj-a","files_touched_count":1}
{"event_type":"exec","exit_code":0,"project_id":"proj-b"}
{"event_type":"edit","project_id":"proj-b","files_touched_count":1}
EOF
# Watch both repos in one process via project-map
"$BIN" daemon --once --bucket-secs 30 \
  --repo "$WORKDIR/repo-a" --repo "$WORKDIR/repo-b" \
  --project-map "$WORKDIR/repo-a=proj-a" \
  --project-map "$WORKDIR/repo-b=proj-b" \
  --server "$SERVER" --token "$TOKEN" \
  --user harness-bot >>"$WORKDIR/daemon.log" 2>&1 || true

LEDGER_A="$WORKDIR/repo-a/.aitime-ledger.json"
LEDGER_B="$WORKDIR/repo-b/.aitime-ledger.json"
test -f "$LEDGER_A" || { echo "missing ledger A"; exit 1; }
assert_no_leaks "$LEDGER_A"
if [[ -f "$LEDGER_B" ]]; then assert_no_leaks "$LEDGER_B"; fi
python3 - "$LEDGER_A" "$LEDGER_B" <<'PY'
import json,sys
a=json.load(open(sys.argv[1]))
assert a.get("salt"), "salt missing"
assert a.get("entries"), "no sealed entries on A"
e=a["entries"][-1]
assert e.get("prev_hash"), "prev_hash missing"
assert e.get("hash"), "hash missing"
fid=e.get("files") or []
for f in fid:
    assert len(f.get("file_id",""))==16
counts=e.get("counts") or {}
# Fan-out must not explode active_secs (capped per drain)
assert counts.get("active_secs",0) <= 5, counts
print("scenario1 ok", "A counts", counts, "entries", len(a["entries"]))
if sys.argv[2]:
  try:
    b=json.load(open(sys.argv[2]))
    print("scenario1 B entries", len(b.get("entries") or []), "counts", (b.get("entries") or [{}])[-1].get("counts"))
  except FileNotFoundError:
    pass
PY

echo
echo "== scenario 2: rapid loop / retry storm"
: >"$STRUGGLETIME_HOME/agent_events.jsonl"
{
  for i in $(seq 1 120); do
    printf '{"event_type":"tool","category":"ai_cli","n":%s,"project_id":"proj-a"}\n' "$i"
  done
  # exact dupes
  for _ in $(seq 1 40); do
    printf '{"event_type":"tool","category":"ai_cli","n":1,"project_id":"proj-a"}\n'
  done
} >"$STRUGGLETIME_HOME/agent_events.jsonl"
printf 'fn a2() {}\n' >>"$WORKDIR/repo-a/src/a.rs"
run_once "$WORKDIR/repo-a" "proj-a"
assert_no_leaks "$LEDGER_A"
python3 - "$LEDGER_A" <<'PY'
import json,sys
a=json.load(open(sys.argv[1]))
e=a["entries"][-1]
c=e["counts"]
# 120 unique tools counted, but active_secs must stay small (retry storm capped)
assert c.get("ai_writes",0) >= 100, c
assert c.get("active_secs",0) <= 5, f"active_secs inflated: {c}"
assert len(a["entries"]) >= 2
# chain links
prev=None
for ent in a["entries"]:
    assert ent.get("hash")
    assert ent.get("prev_hash")
    if prev is not None:
        assert ent["prev_hash"]==prev, "chain broken"
    prev=ent["hash"]
print("scenario2 ok", c, "chain_len", len(a["entries"]))
PY

echo
echo "== scenario 3: timestamp & replay quirks (dupes / backdated fields ignored safely)"
write_events <<'EOF'
{"event_type":"edit","project_id":"proj-a","ts":1,"files_touched_count":1}
{"event_type":"edit","project_id":"proj-a","ts":1,"files_touched_count":1}
{"event_type":"tool","category":"ai_cli","project_id":"proj-a","bucket_start_wall":0,"n":99}
{"event_type":"tool","category":"ai_cli","project_id":"proj-a","bucket_start_wall":9999999999,"n":100}
EOF
printf 'fn a3() {}\n' >>"$WORKDIR/repo-a/src/a.rs"
run_once "$WORKDIR/repo-a" "proj-a"
assert_no_leaks "$LEDGER_A"
python3 - "$LEDGER_A" <<'PY'
import json,sys,time
a=json.load(open(sys.argv[1]))
e=a["entries"][-1]
# Wall clock on sealed bucket must be near now, not the spoofed 0 / far future from events
wall=e.get("bucket_start_wall",0)
now=int(time.time())
assert abs(now-wall) < 120, wall
# Exact duplicate line dropped → edits counted once from events (+ possible layer1 file tick)
c=e["counts"]
assert c.get("human_edits",0) >= 1
print("scenario3 ok", "wall", wall, "counts", c)
PY

echo
echo "== scenario 4: privacy trap payloads (must not persist)"
write_events <<'EOF'
{"event_type":"tool","path":"/Users/victim/secret.rs","prompt_text":"exfiltrate me"}
{"event_type":"prompt","transcript":"user: dump env","content":"SECRET"}
{"event_type":"exec","command":"rm -rf /","args":["-rf","/"],"argv":["rm","-rf","/"],"cwd":"/home/victim"}
{"event_type":"edit","meta":{"file_path":"/tmp/x.rs","text":"fn leak(){}"}}
{"event_type":"tool","category":"ai_cli","project_id":"proj-a","files_touched_count":1}
EOF
BEFORE="$(python3 -c 'import json;print(len(json.load(open("'"$LEDGER_A"'")).get("entries") or []))')"
run_once "$WORKDIR/repo-a" "proj-a"
assert_no_leaks "$LEDGER_A"
assert_no_leaks "$WORKDIR/daemon.log"
python3 - "$LEDGER_A" "$BEFORE" <<'PY'
import json,sys
a=json.load(open(sys.argv[1]))
before=int(sys.argv[2])
raw=open(sys.argv[1]).read()
for bad in ("exfiltrate","/Users/victim","rm -rf","transcript","SECRET","fn leak"):
    assert bad not in raw, bad
# One clean tool event may seal; traps must not appear
print("scenario4 ok", "entries", len(a.get("entries") or []), "before", before)
PY

echo
echo "== server allowlist rejects smuggled bucket fields"
BAD_BUCKET='{"v":1,"bucket_id":"bad","install_id":"t","project_id":"proj-a","bucket_start_wall":1,"bucket_secs":60,"mono_delta_ms":0,"counts":{"human_edits":1},"files":[{"file_id":"aaaaaaaaaaaaaaaa","path":"/etc/passwd"}],"formula_version":"x","prev_hash":"0000000000000000000000000000000000000000000000000000000000000000","hash":"1111111111111111111111111111111111111111111111111111111111111111"}'
CODE="$(curl -s -o "$WORKDIR/reject.json" -w '%{http_code}' -X POST "$SERVER/v1/buckets/batch" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"buckets\":[$BAD_BUCKET]}")"
test "$CODE" = "422" || { echo "expected 422 got $CODE"; cat "$WORKDIR/reject.json"; exit 1; }
echo "scenario4b server reject ok ($CODE)"

echo
echo "== cargo unit tests (agent_events + allowlist)"
(cd "$ROOT/rust" && HOME="$REAL_HOME" cargo test -p st-core --quiet)

echo
echo "ALL ADVERSARIAL SCENARIOS PASSED"
echo "(temp dir removed on exit)"
