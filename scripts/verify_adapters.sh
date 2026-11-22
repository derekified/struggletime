#!/bin/sh
# Adapter presence + privacy checks + e2e ingest against the unified Rust binary.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fail=0

need() {
  if [ ! -e "$ROOT/$1" ]; then
    echo "MISSING $1" >&2
    fail=1
  else
    echo "ok  $1"
  fi
}

need adapters/vscode/extension.js
need adapters/vscode/package.json
need adapters/claude-code/hooks.json
need adapters/codex/wrap.sh
need adapters/neovim/lua/struggletime/init.lua
need adapters/godot/addons/struggletime/plugin.cfg
need adapters/jetbrains/src/main/resources/META-INF/plugin.xml
need adapters/unity/Editor/StruggleTimeTracker.cs
need adapters/openclaw/README.md
need adapters/openclaw/run.sh
need daemon/hooks.sh
need daemon/hooks.ps1

# Forbidden content keys must not appear as emit templates in adapter sources
if grep -RInE '"(content|prompt_text|filepath|file_path|keystrokes|screenshot)"\s*:' \
  "$ROOT/adapters" "$ROOT/daemon/hooks.sh" "$ROOT/daemon/hooks.ps1" 2>/dev/null \
  | grep -v README | grep -v Binary; then
  echo "FAIL forbidden content-shaped keys in adapters" >&2
  fail=1
else
  echo "ok  no forbidden content keys in adapter emit templates"
fi

# Split patterns so this script file is not a self-match.
if git -C "$ROOT" grep -nIE 'hacka''time-next|Home''brew|\bHacka''time\b' -- \
  ':!Cargo.lock' ':!docs-site/package-lock.json' ':!scripts/verify_adapters.sh' 2>/dev/null; then
  echo "FAIL legacy brand strings found" >&2
  fail=1
else
  echo "ok  no legacy brand strings"
fi

# --- E2E against unified binary ---
BIN="$ROOT/rust/target/release/struggletime"
if [ ! -x "$BIN" ]; then
  echo "building struggletime…"
  ( cd "$ROOT/rust" && cargo build --release -p struggletime )
fi
need_bin() {
  if [ ! -x "$BIN" ]; then
    echo "MISSING $BIN" >&2
    exit 1
  fi
}
need_bin
echo "ok  binary $BIN"

BASE=$(mktemp -d /tmp/st-adapters-XXXXXX)
export STRUGGLETIME_HOME="$BASE/runtime"
export STRUGGLETIME_CONFIG="$BASE/config"
export AITIME_HOME="$STRUGGLETIME_HOME"
export AITIME_CONFIG="$STRUGGLETIME_CONFIG"
mkdir -p "$STRUGGLETIME_HOME" "$STRUGGLETIME_CONFIG" "$BASE/proj"
echo 'seed' > "$BASE/proj/main.rs"
EVENTS="$STRUGGLETIME_HOME/agent_events.jsonl"
EXEC="$STRUGGLETIME_HOME/exec.jsonl"
: > "$EVENTS"
: > "$EXEC"

# Simulate allowlisted emits from every adapter family (labels only — never paths/prompts/argv)
emit() { printf '%s\n' "$1" >> "$EVENTS"; }
emit_exec() { printf '%s\n' "$1" >> "$EXEC"; }

# vscode / neovim / jetbrains / godot / unity — editor heartbeats
emit '{"event_type":"edit","category":"editor","adapter":"vscode"}'
emit '{"event_type":"save","category":"editor","adapter":"neovim"}'
emit '{"event_type":"focus","category":"editor","adapter":"jetbrains"}'
emit '{"event_type":"edit","category":"editor","adapter":"godot"}'
emit '{"event_type":"play","category":"editor","adapter":"unity"}'

# claude-code / codex / openclaw — agent boundaries
emit '{"event_type":"prompt","category":"ai_cli","adapter":"claude-code"}'
emit '{"event_type":"tool","category":"ai_cli","adapter":"claude-code"}'
emit '{"event_type":"prompt","category":"ai_cli","adapter":"codex"}'
emit '{"event_type":"tool","category":"ai_cli","adapter":"openclaw"}'

# shell hooks — exec categories only
emit_exec '{"event_type":"exec","category":"test","exit_code":0,"adapter":"hooks"}'
emit_exec '{"event_type":"exec","category":"build","exit_code":0,"adapter":"hooks"}'
emit_exec '{"event_type":"exec","category":"run","exit_code":1,"adapter":"hooks"}'

# Reject-path: forbidden keys must be ignored by daemon (plant one bad line)
emit '{"event_type":"edit","category":"editor","path":"/tmp/secret.rs","content":"leak"}'

echo "ok  planted multi-adapter events"

"$BIN" daemon --repo "$BASE/proj" --project adapter-e2e --bucket-secs 30 --once --debug \
  >"$BASE/daemon.log" 2>&1 || {
  echo "FAIL daemon --once" >&2
  cat "$BASE/daemon.log" >&2
  exit 1
}

LEDGER="$BASE/proj/.aitime-ledger.json"
if [ ! -f "$LEDGER" ]; then
  echo "FAIL no ledger after adapter ingest" >&2
  cat "$BASE/daemon.log" >&2
  exit 1
fi
echo "ok  ledger sealed"

python3 - "$LEDGER" <<'PY'
import json, sys, re
d = json.load(open(sys.argv[1]))
assert d.get("entries"), "empty ledger"
e = d["entries"][0]
c = e.get("counts") or {}
print("counts", c)
assert (c.get("human_edits", 0) + c.get("ai_writes", 0) + c.get("prompts", 0) + c.get("executions", 0)) >= 1
blob = json.dumps(d)
for bad in ("/tmp/secret", "main.rs", '"path"', "prompt_text", "leak", "secret.rs"):
    assert bad not in blob, f"privacy leak: {bad}"
for f in e.get("files") or []:
    fid = f.get("file_id") or ""
    assert re.fullmatch(r"[0-9a-f]{16}", fid), fid
print("ok  privacy + salted file_ids")
PY

"$BIN" verify --ledger "$LEDGER"
echo "ok  hash chain verify"

# Second wave → chain grows
emit '{"event_type":"edit","category":"editor","adapter":"vscode"}'
"$BIN" daemon --repo "$BASE/proj" --project adapter-e2e --bucket-secs 30 --once >"$BASE/daemon2.log" 2>&1
"$BIN" verify --ledger "$LEDGER"
python3 - "$LEDGER" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
assert len(d["entries"]) >= 2
for i in range(1, len(d["entries"])):
    assert d["entries"][i]["prev_hash"] == d["entries"][i-1]["hash"]
print("ok  multi-seal chain")
PY

# SQLite ingest via local serve
DB="$BASE/server.db"
"$BIN" serve --bind 127.0.0.1 --port 18778 --db "$DB" --static-dir "$ROOT/server/static" \
  >"$BASE/server.log" 2>&1 &
SPID=$!
trap 'kill $SPID 2>/dev/null || true; rm -rf "$BASE"' EXIT
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  if curl -sf "http://127.0.0.1:18778/health" >/dev/null; then break; fi
  sleep 0.15
done
KEY=$(curl -sS -X POST "http://127.0.0.1:18778/v1/keys" \
  -H 'Content-Type: application/json' -d '{"handle":"adaptertest"}')
TOKEN=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['token'])" "$KEY")
BODY=$(python3 - "$LEDGER" <<'PY2'
import json,sys
d=json.load(open(sys.argv[1]))
print(json.dumps({"buckets": d.get("pending") or d.get("entries")}))
PY2
)
UP=$(curl -sS -w "%{http_code}" -o "$BASE/up.json" -X POST "http://127.0.0.1:18778/v1/buckets/batch" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d "$BODY")
test "$UP" = "200"
python3 -c "import json; j=json.load(open('$BASE/up.json')); assert j.get('ok') and j.get('accepted',0)>=1; print('ok  sqlite ingest accepted', j.get('accepted'))"

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "ok  all adapter checks"
