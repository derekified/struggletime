# Architecture: telemetry + provenance + causal verification

Not a stopwatch. Three pipelines, all metadata-only, no LLM in the hot path.

## 1. Causal weighting (kills the "formula" risk)

Daemon classifies raw events into 5-min buckets. Server (or daemon — same
rules, `weighting/rules-v0.1.0.yaml`) scores each bucket by state transition,
not activity volume:

```
(prompt|human_edit) -> (test|build|run) -> (follow-up edit|commit) = 1.0x
failure -> fix -> pass                                            = 1.0x (struggle loop)
AI writes, no dwell, no follow-up, straight to commit             = 0.20x
human edits, never executed                                       = 0.70x
idle >= 240s of the bucket                                        = 0.0x
```

Every bucket carries `formula_version`. Reviewer deflations require a
reason code; those codes are the only training signal for the next version.

## 2. Provenance (kills fake heartbeats)

- **Hash chain:** `hash = SHA256(canonical_bucket || prev_hash)`, plus
  `mono_delta_ms` from a monotonic clock (wall time is display-only).
  Editing the local SQLite buffer breaks the chain at review time.
  Server cost: ~0 until submission, one SHA256 per bucket on ingest.
- **Per-install keypair:** Ed25519 signature over `hash`. Not DRM — it
  stops bulk retroactive fabrication, not a determined owner.
- **Git anchor:** every bucket stores `head_commit`, `dirty_files`, and
  `git diff --shortstat` totals. A submission overlays the weighted
  timeline on the `head_from..head_to` commit graph. 14 claimed hours
  producing an 18-line diff is visible instantly, with zero content logged.

Reviewer queue auto-flags: hours/KLOC, 100%-success executions,
AI:human edit ratio (see `weighting/rules-v0.1.0.yaml`).

## 3. Universal core (kills harness fragmentation)

| Layer | What | Coverage |
|---|---|---|
| L1 filesystem watcher | `notify`/inotify/FSEvents on git repos (`.gitignore`-aware); burst shape separates typing (1–2 files, frequent small writes) from agent dumps (5+ files / >100 lines in <1s) | 100% |
| L1 shell hook | 5-line `preexec/precmd` (bash/zsh/fish/PowerShell) → local socket; sends category (`git`/`test_runner`/`compiler`/`ai_cli`/`other`), duration, exit code only | 100% |
| L2 adapters | VS Code/Cursor `onDidChangeTextDocument` + focus; Claude Code `PostToolUse`/`Stop` hooks; Codex wrapper | enrichment only |

Adapters pipe to the daemon over a Unix socket / named pipe. If an
adapter breaks next week, L1 still counts the session.

## Privacy boundary

Raw events, commands, prompts, and code are classified and aggregated
**on-device** and never sent. Wire payload is the bucket in
`schemas/heartbeat.schema.json`. Timelapse (later) stays local unless
its hours are submitted. Public profiles show aggregates only.

## Budget (~$40–60/mo at StruggleTime scale)

- Client rollup: 109,759 h/mo ÷ 5-min buckets ≈ 1.32M buckets/mo ≈
  **0.5 writes/sec average** — trivial for one Postgres.
- Single VPS (Go/Rust + Postgres, monthly-partitioned `bucket` table,
  `schemas/server.sql`) + R2/S3 (~$2/mo) for frozen evidence packs.
- No LLM inference, no per-event server round-trips, no streaming infra.

## Files

- `schemas/local-ledger.sql` — on-device SQLite (raw events, buckets, chain)
- `schemas/server.sql` — Postgres (buckets, submissions, reviews)
- `schemas/heartbeat.schema.json` — wire protocol v1
- `weighting/rules-v0.1.0.yaml` — causal state machine + reason codes
- `weighting/calibration-20.md` — 20-session calibration protocol
- `src/verify.py` — reference chain verifier + weighting engine (stdlib only)

## Agent-first, metadata-only evidence (v0.2)

Most agent users never open an IDE, so Layer 1 (terminal + filesystem)
is the product and IDE adapters are decoration. Evidence never needs
pixels or keystrokes:

- **25–50 KB per submitted session** of pure metadata: anonymized file
  ids (salted SHA256, salt stays on-device), line deltas, edit bursts,
  build/test exit codes, git anchors. A typical hour is ~10 KB.
- **Structural friction timeline:** reviewers see friction (failures,
  retries, edits, follow-through, 0–1 per bucket) as bars overlaid on
  the git commit graph (`GET /v1/projects/{id}/friction`, rendered in
  the dashboard) — the shape of struggle, with no content to leak.
- **Silent-debug decay:** silent buckets hold 0.70× for 5 minutes,
  decay linearly to 0 at 12 minutes, then stop. Reading and thinking
  are not cliffed to zero, and silence cannot farm forever.
- **Strict schema allowlist:** the server rejects any bucket carrying a
  field outside `schemas/heartbeat.schema.json` — `prompt_text`,
  `command`, `content`, file paths, etc. get a 400 before chaining.
  Raw text/pixels are not merely unused; they are unrepresentable.
- **Local inspect:** `python3 daemon/inspect.py <repo|buckets.json>`
  prints byte-for-byte what will be sent, its size, chain validity, and
  allowlist status. Verify before you trust.
