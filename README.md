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
make check                                   # python, git, test suite
make server                                  # dashboard + API on :18765
sh daemon/install.sh   # once, per machine
struggletime-helper start --repo ~/my-project --user you
struggletime-helper status                   # logs: struggletime-helper logs
```

Open http://127.0.0.1:18765 — projects, weighted hours, streaks, and
flags update live. No dependencies beyond Python 3.10+ (stdlib only).
Just looking? `make demo` starts a seeded server on :18766.

**docs:** [setup](docs/setup.md) · [daemon](docs/daemon.md) ·
[api](docs/api.md) · [integrations](docs/integrations.md) ·
[deployment](docs/deployment.md) · [architecture](docs/architecture.md) ·
[troubleshooting](docs/troubleshooting.md) ·
[contributing](CONTRIBUTING.md) · env vars: [`.env.example`](.env.example)

## How each bounty requirement is met

| # | Requirement | Implementation |
|---|---|---|
| 1 | AGPLv3 | `LICENSE`, SPDX headers |
| 2 | Accurate struggle hours | Causal state machine in `src/verify.py` (`v0.4.0-alpha`); mirror `weighting/rules-v0.4.0.yaml`; calibration in `weighting/calibration-20.md` |
| 3 | Motivating, not anti-fraud | Live dashboard: weighted h, streaks, **weekly goal**, public builder profiles |
| 4 | Passive | Daemon + shell hook; no timers or start/stop |
| 5 | Central server | Accounts (token auth), public-by-default profiles, projects + git linking, live stats (15s poll + SSE keepalive), github/slack integrations |
| 6 | Privacy-friendly | metadata-only buckets; schema allowlist; `daemon/tracker_inspect.py`; logged ≠ submitted |
| 7 | All models/harnesses | L1 terminal+fs core; L2 adapters in `adapters/` (VS Code, Claude Code, Codex wrap, OpenClaw) |
| 8 | Win/Mac/Linux, low-spec | Polling daemon, stdlib core; `daemon/install.sh` + `install.ps1`; `scripts/package.sh` tarball |
| 9 | Trustworthy | Hash-chain + **Ed25519 per-install signatures** + git anchors + outlier flags + reason-coded review |
| 10 | Auditable | Submission freezes evidence pack; reviewers approve/deflate with reason codes |
| 11 | ≤$500/mo | Client rollup ≈0.5 writes/s; single VPS ~$40–60/mo; no LLM hot path |

Acceptance path: 20-session calibration (**95% agreement**, `weighting/calibration-20.md`)
→ 100-project pilot → 1,000 weighted projects. Postgres schema ready in
`schemas/server.sql`.

## Test

```bash
python3 -m unittest discover -s tests   # 51 tests: verifier, metadata/privacy, server api, daemon end-to-end, malformed/edge cases, openclaw
python3 src/verify.py examples/session-closed-loop.json
```

Layout: `server/` API+dashboard · `daemon/` Layer-1 + hooks ·
`adapters/` L2 enrichment · `schemas/` SQLite/Postgres/wire v1 ·
`weighting/` formula + calibration · `src/verify.py` reference engine ·
`examples/` chained session fixtures.
