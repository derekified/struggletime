# Calibration: 20 labelled sessions

Goal: prove reviewers agree with `v0.1.0-alpha` before any teen relies on it.

## Protocol

1. Recruit 20 sessions across archetypes: human-only, AI-pairing,
   agent-heavy, reading/debugging (low edits), boilerplate generation.
   Screen-record locally (never uploaded) as ground truth.
2. Two reviewers independently label each 5-min bucket:
   `FULL` / `PARTIAL` / `NONE` + reason code if not `FULL`.
3. Run `src/verify.py` on the same buckets. Compare per-bucket weight
   against reviewer consensus; report agreement and mean deflation.
4. Tune **only** thresholds in `rules-v0.1.0.yaml` (idle pause, dwell
   cutoff, open-loop weight). Bump `formula_version` on any change.
5. Gate to Phase 1: ≥80% bucket agreement and ≤20% mean deflation on
   AI-pairing + human-only sessions. Reading/debugging is expected to
   undercount in v0 — document it, don't surveillance your way out of it.

## Then: 100-project pilot

Same loop at project level. Gate to scale: submitted-hour approval rate
materially above Stardance's 22%, reviewer time < 5 min per submitted
project, and no reason code dominating (>40%) — a dominant code means
the formula, not the teens, is wrong.
