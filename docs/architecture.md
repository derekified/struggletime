# Architecture: telemetry, provenance, and scoring

Not a stopwatch. Three pipelines, all metadata-only, no LLM on the ingest path.

## 1. Causal weighting

Daemon classifies raw events into buckets (default 5 min). Server and daemon
share the same rules in `src/verify.py` (`FORMULA_VERSION`). YAML under
`weighting/rules-v0.4.0.yaml` is a human-readable mirror — not the engine.

```
AI struggle / fail→fix→pass / closed loop     = 1.0x
AI writes + dwell + follow-up                  = 0.85x
human edits, never executed                    = 0.70x
AI dump / stare, no follow-up                  = 0.20x
silent follow-up                               = 0.70 → 0 over 5–12 minutes
```

Active-span billing: `billable_secs = min(bucket_secs, active_secs)` when
the bucket is not silent. Every bucket carries `formula_version`. Reviewer
deflations require a reason code; those codes tune the next version.

## 2. Provenance

- **Hash chain:** `hash = SHA256(canonical_bucket || prev_hash)`, plus
  `mono_delta_ms` from a monotonic clock (wall time is display-only).
  Editing the local ledger breaks the chain at review time.
- **Per-install Ed25519:** `src/install_sig.py` generates a keypair under
  `~/.config/struggletime/`. Buckets carry `install_pubkey` + `signature`
  over the hash. Server stores the first pubkey per `install_id` and
  rejects mismatches / bad signatures. Unsigned buckets are still accepted
  (migration) but counted as `unsigned_buckets`. Not DRM — stops bulk
  fabrication without the private key, not a determined owner.
- **Git anchor:** every bucket stores `head_commit`, `dirty_files`, and
  diff totals. A submission overlays the weighted timeline on the
  `head_from..head_to` commit graph.

## 3. Universal core

| Layer | What | Coverage |
|---|---|---|
| L1 polling watcher | Stdlib `stat` poll of git repos (`.gitignore`-aware); burst shape separates typing from agent dumps | 100% |
| L1 shell hook | `preexec/precmd` (bash/zsh) + PowerShell prompt hook → local JSONL; category, duration, exit code only | 100% |
| L2 adapters | VS Code/Cursor; Claude Code hooks; Codex `wrap.sh`; OpenClaw session tail; Neovim; Godot; JetBrains; Unity | enrichment only |

Adapters append to `~/.aitime/agent_events.jsonl`. If an adapter breaks,
L1 still counts the session.

## Privacy boundary

Raw events, commands, prompts, and code are classified on-device and never
sent. Wire payload matches `schemas/heartbeat.schema.json`. Public profiles
show aggregates only. Logged ≠ submitted.

## Budget (~$40–60/mo at community scale)

- Client rollup: 109,759 h/mo ÷ 5-min buckets ≈ **0.5 writes/sec**.
- Single VPS (Python + SQLite today; `schemas/server.sql` for Postgres) —
  no LLM in the hot path.

## Files

- `src/verify.py` — reference verifier + weighting (source of truth)
- `src/install_sig.py` — Ed25519 sign/verify
- `weighting/rules-v0.4.0.yaml` — mirror of the formula
- `weighting/calibration-20.md` — labelled session results
- `schemas/heartbeat.schema.json` — wire allowlist mirror
- `daemon/` — Layer-1 + hooks + tray/helper
- `adapters/` — optional L2 enrichment
