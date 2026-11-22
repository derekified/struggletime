# Calibration: 20 labelled sessions

**Formula:** `v0.4.0-alpha` (source: `rust/crates/st-core`)
**Result:** 20/20 buckets agree with reviewer labels (**100.0%**).
**Gate (≥80%):** PASS

## Method

Synthetic sessions covering human-only, AI-pairing, agent dumps, struggle
loops, silent/reading, and tomography counters. Each session is one 5-min
bucket with an a-priori reviewer label `FULL` / `PARTIAL` / `NONE`.
`weighting/calibration-sessions/` and this report.

Reading/debugging silent buckets correctly land as PARTIAL (0.70), not
surveillance. Agent dumps land as NONE (0.20). Struggle loops are FULL.

## Per-session results

| # | Archetype | Expected | Got | Weight | State | Agree |
|---|---|---|---|---|---|---|
| 1 | `human_closed_loop` | FULL | FULL | 1.0 | `CLOSED_LOOP` | yes |
| 2 | `human_unverified` | PARTIAL | PARTIAL | 0.7 | `HUMAN_UNVERIFIED` | yes |
| 3 | `struggle_fail_pass` | FULL | FULL | 1.0 | `STRUGGLE_LOOP` | yes |
| 4 | `ai_struggle_reedit` | FULL | FULL | 1.0 | `AI_STRUGGLE` | yes |
| 5 | `ai_struggle_cache` | FULL | FULL | 1.0 | `AI_STRUGGLE` | yes |
| 6 | `ai_reviewed` | PARTIAL | PARTIAL | 0.85 | `AI_REVIEWED` | yes |
| 7 | `open_loop_dump` | NONE | NONE | 0.2 | `OPEN_LOOP_AGENT_DUMP` | yes |
| 8 | `ai_unreviewed` | NONE | NONE | 0.25 | `AI_UNREVIEWED` | yes |
| 9 | `exec_only` | PARTIAL | PARTIAL | 0.5 | `EXEC_ONLY` | yes |
| 10 | `silent_short` | PARTIAL | PARTIAL | 0.7 | `SILENT_DEBUG` | yes |
| 11 | `ai_pairing_prompt` | FULL | FULL | 1.0 | `CLOSED_LOOP` | yes |
| 12 | `agent_retry_loop` | FULL | FULL | 1.0 | `AI_STRUGGLE` | yes |
| 13 | `boilerplate_stare` | NONE | NONE | 0.1524 | `OPEN_LOOP_AGENT_DUMP_DECAY` | yes |
| 14 | `tomography_atomic` | FULL | FULL | 1.0 | `AI_STRUGGLE` | yes |
| 15 | `build_cycles` | FULL | FULL | 1.0 | `AI_STRUGGLE` | yes |
| 16 | `git_plumbing_thrash` | FULL | FULL | 1.0 | `AI_STRUGGLE` | yes |
| 17 | `reading_decay_edge` | PARTIAL | PARTIAL | 0.7 | `SILENT_DEBUG` | yes |
| 18 | `idle_decay_human` | PARTIAL | PARTIAL | 0.5333 | `HUMAN_UNVERIFIED_DECAY` | yes |
| 19 | `formatter_not_dump` | NONE | NONE | 0.2 | `OPEN_LOOP_AGENT_DUMP` | yes |
| 20 | `empty_fallback` | PARTIAL | PARTIAL | 0.7 | `SILENT_DEBUG` | yes |

## Protocol notes

1. Fixtures are synthetic stand-ins for screen-recorded ground truth.
2. Tune only via labelled sessions + pilot — never raw production hours.
3. Next gate: 100-project pilot with reviewer time < 5 min / submission.

