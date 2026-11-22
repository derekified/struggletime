# Contributing

```bash
git clone https://github.com/derekified/struggletime.git
cd struggletime
make check    # before you open a PR
```

Rules of the repo, in order:

1. **Privacy is the product.** A change that lets raw text, paths, prompts,
   command arguments, or pixels reach the server is rejected on sight —
   including in tests and logs. The allowlist test is not negotiable.
2. **Weights are versioned, never silently edited.** Changing the formula
   means a new `formula_version` and calibration notes in `weighting/`.
3. **Tests first for trust paths.** Chain verification, allowlist, weighting
   states, integration signatures: if it guards trust, it ships with a
   failing-then-passing test.
4. **Stdlib only** in `server/`, `daemon/`, and `src/` unless a change
   explains why a dependency earns its keep on an old laptop.
5. Keep docs and UI copy plain and direct. No buzzword filler.

Good first work: a new adapter under `adapters/` (append counts to the
agent event log, throw content away), calibration sessions per
`weighting/calibration-20.md`, or trying to fake hours against your own
server and reporting how.
