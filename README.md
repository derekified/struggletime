# StruggleTime

**AI time tracking that counts productive struggle, not activity.**
Built for Hack Club's $10,000 AI time-tracking bounty. AGPLv3.
Pitch: [`PITCH.md`](PITCH.md) · Architecture: [`docs/architecture.md`](docs/architecture.md)

An hour an AI runs unattended is not an hour a teenager learned
anything. StruggleTime passively detects the causal loop of real work —
`(prompt|edit) → (test|build) → (follow-up edit|commit)` — weights it,
hash-chains it so it can't be quietly rewritten, anchors it to git, and
shows it back as streaks and weighted hours worth chasing.

## Run it (60 seconds)

```bash
python3 server/server.py            # dashboard + API on :18765
# in another terminal, track a real repo:
AITIME_EXEC_LOG=~/.aitime/exec.jsonl python3 daemon/daemon.py --repo ~/my-project --server http://127.0.0.1:18765 --user you
source daemon/hooks.sh              # bash/zsh (Windows: daemon/hooks.ps1)
```

Open http://127.0.0.1:18765 — projects, weighted hours, streaks, and
flags update live. No dependencies beyond Python 3.10+ (stdlib only).

## How each bounty requirement is met

| # | Requirement | Implementation |
|---|---|---|
| 1 | AGPLv3 | `LICENSE`, SPDX headers |
| 2 | Accurate struggle hours | Causal state machine, `weighting/rules-v0.1.0.yaml` |
| 3 | Motivating, not anti-fraud | Live dashboard: weighted h, streaks, weekly goal, public builder profiles |
| 4 | Passive | Daemon + shell hook; no timers or start/stop |
| 5 | Central server | Accounts (token auth), public-by-default profiles, projects + git linking, realtime stats + SSE |
| 6 | Privacy-friendly | metadata-only 5-min buckets (25–50 kb/session, anonymized file ids, line deltas, exit codes); strict schema allowlist rejects raw text/paths with 400; `daemon/inspect.py` shows byte-for-byte what leaves; logged ≠ submitted |
| 7 | All models/harnesses | agent-first: layer-1 terminal+fs core (100%, most agent users never open an ide); l2 adapters are optional enrichment (`adapters/`) |
| 8 | Win/Mac/Linux, low-spec | Polling daemon, stdlib only, ~46 MB server RSS; hooks for bash/zsh/PowerShell |
| 9 | Trustworthy | Hash-chain + git anchors + outlier flags + reason-coded review (`server/`) |
| 10 | Auditable | Submission freezes evidence pack; reviewers approve/deflate with reason codes |
| 11 | ≤$500/mo | Client rollup ≈0.5 writes/s at 109,759 h/mo; single VPS ~$40–60/mo; no LLM hot path |

Acceptance path: 20-session calibration (`weighting/calibration-20.md`)
→ 100-project pilot → 1,000 weighted projects in the Unified Projects DB.
Packaged installers and the Postgres deployment (`schemas/server.sql`)
are the remaining scale work; every protocol they need is fixed here.

## Test

```bash
python3 -m unittest discover -s tests   # 36 tests: verifier, metadata/privacy, server api, daemon end-to-end, malformed/edge cases
python3 src/verify.py examples/session-closed-loop.json
```

Layout: `server/` API+dashboard · `daemon/` Layer-1 + hooks ·
`adapters/` L2 enrichment · `schemas/` SQLite/Postgres/wire v1 ·
`weighting/` formula + calibration · `src/verify.py` reference engine ·
`examples/` chained session fixtures.
