# AI Time Tracking — MVP Brief

Summarises Hack Club's $10,000 AI time-tracking bounty in MVP terms.
Full visual brief: see the canvas linked from the agent chat, or read below.

## In one sentence

Passively prove a teenager actually struggled with a project — not just
that an AI ran — and make seeing that proof motivating enough that they
keep it on when nobody is paying them.

**Win condition (unchanged):** first AGPLv3 open-source system used to
track 1,000 weighted projects in the Hack Club Unified Projects DB.

| Constraint | Value |
|---|---|
| Target scale | 15,488 MAU, 109,759 h/month |
| Hosting cap | ≤ $500/month (StruggleTime effective: ~$55/month) |
| Baseline to beat | Stardance: 123,400 h submitted, 27,600 approved (22%) |

## The one definition to ship

**Weighted hour = human-steered active time, discounted for unattended AI time.**

Count in full: prompting, editing, testing, debugging, reviewing AI output
then acting on it. Discount/pause: no human input 5+ min, agent running
unattended, AI output with no follow-up edit/test/commit. Publish and
version the formula — trust comes from transparency, not secrecy.

## Core loop

1. **Install** — tiny agent auto-detects project/git repo. No timers.
2. **Build** — passive metadata heartbeats from editor, terminal, AI harness.
3. **See progress** — realtime dashboard: weighted hours, streaks, projects.
4. **Ship + submit** — link git/shipped project, declare AI use, submit.
5. **Review** — reviewer approves/deflates an evidence pack with a reason.

## MVP scope

**Must (v1 to win):** passive agent for Windows/Mac/Linux on low-spec
hardware; plugins for VS Code + Cursor, Claude Code + Codex, plus a
git/terminal fallback for every other harness; metadata-only logging
(timestamps, project, edit/test/commit events, AI session + prompt counts —
never prompt/code content by default); rules-based weighting (no per-hour
LLM judging); server with accounts, public profiles, projects, GitHub
linking, realtime stats; submit flow with mandatory AI-use declaration;
reviewer queue with auditable evidence pack per submission.

**Later:** plugin SDK for all harnesses, local timelapse uploaded only for
submitted hours, LLM spot-checks on flagged submissions, signed heartbeats.

**Never:** keylogging/screen surveillance, perfect fraud-proofing
(auditable ≠ ungameable), every AI tool on day one, replacing StruggleTime
for non-AI coding.

## System in three boxes

- **Local agent** — small binary/extension, batches tiny JSON heartbeats
  every 1–2 min, local-first storage, syncs when online.
- **Server** — one Postgres + one app server, applies weighting, pushes
  realtime updates (SSE/websockets), object storage only for submitted
  evidence.
- **Review** — evidence pack = weighted timeline + git history + AI
  declaration + heartbeat summary. Deflation rate is the quality metric.

## Privacy split

- **Logged hours (private):** metadata on device, aggregates on server.
  Public profile shows totals/streaks/projects only. Export/delete anytime.
- **Submitted hours (auditable):** submitting freezes and uploads that
  session's evidence pack (optional timelapse if enabled), with explicit
  per-submission consent.

## Build order

| Phase | Ship | Exit test |
|---|---|---|
| 0 — Prove | Weighting rules vs. hand-labelled sessions (~20 teens) | Reviewers agree with formula |
| 1 — Track | Agent (3 OSes) + 4 plugins + realtime dashboard | Teens leave it installed |
| 2 — Submit | Git linking, AI declaration, reviewer queue | 100-project pilot, low deflation |
| 3 — Scale | Plugin SDK, hardening, cost tuning | 1,000 weighted projects, ≤ $500/mo |

## Success metrics

- **Trust:** submitted-hour approval rate (vs. 22% baseline), reviewer time/hour
- **Motivation:** retention with no program attached
- **Accuracy:** weighted vs. hand-reviewed hours on a labelled sample
- **Cost:** $/MAU and $/tracked hour at StruggleTime scale

## Biggest risks — and the architecture that neutralises them

Framing: telemetry + cryptographic provenance + causal verification,
not a stopwatch. Full detail in `docs/architecture.md`.

1. **"Productive struggle" resists any formula** → causal state machine:
   `(prompt|edit) → (test|build) → (follow-up edit|commit)` = 1.0×,
   failure→fix→pass = 1.0×, unattended agent dump = 0.20×, idle = 0×.
   Versioned formula; reviewer deflations require a reason code that
   tunes the next version. Rules: `weighting/rules-v0.1.0.yaml`,
   calibration: `weighting/calibration-20.md`.
2. **Heartbeats can be faked** → hash-chained local ledger
   (`prev_hash` + monotonic clock + per-install signature) plus git
   anchors (`head_commit` + diff totals per bucket). Tampering breaks
   the chain; claimed hours are overlaid on the commit graph at review.
   Reviewer flags: hours/KLOC, 100%-success executions, AI:human ratio.
3. **Harness fragmentation** → editor-agnostic daemon core: filesystem
   watcher + 5-line shell hook (category/duration/exit code only) covers
   100% of setups; VS Code/Cursor and Claude Code/Codex adapters are
   thin enrichment only.

Budget stays ~$40–60/mo: client-side 5-min rollup ≈ 0.5 writes/sec at
109,759 h/mo, single VPS + Postgres, no LLM in the hot path.

## Reference implementation

```bash
python3 src/verify.py examples/session-closed-loop.json  # 0.25 weighted h, clean chain
python3 src/verify.py examples/session-open-loop.json    # discounted + flagged
python3 src/verify.py examples/session-tampered.json     # chain error, exit 1
python3 -m unittest discover -s tests                    # 4 tests
```

Schemas: `schemas/local-ledger.sql` (SQLite), `schemas/server.sql`
(Postgres, partitioned), `schemas/heartbeat.schema.json` (wire v1).

## Server test results (2026-11-19)

Reference server in `server/` (stdlib Python + SQLite; contract = `schemas/server.sql`):

- 12/12 unit + API tests pass (`python3 -m unittest discover -s tests`)
- Live: closed-loop batch → `201, 0.25 weighted h`; tampered batch → `422`;
  submission → review (bad reason `400`, valid `200 approved`)
- Load: 5,000 hash-chained buckets ingested + verified in **0.19s**
  (~26k/sec locally vs ~0.5/sec needed at 109,759 h/mo), ~46 MB RSS
