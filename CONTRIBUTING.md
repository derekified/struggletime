# Contributing

```bash
git clone https://github.com/derekified/struggletime.git
cd struggletime
make check    # before you open a PR
```

Rules of the repo, in order:

1. **Privacy is the product.** A change that lets raw text, paths, prompts,
   command arguments, or pixels reach the server is rejected on sight —
   including in tests and logs. The allowlist check is not negotiable.
2. **Weights are versioned, never silently edited.** Changing the formula
   means a new `formula_version` and calibration notes in `weighting/`.
3. **Tests first for trust paths.** Chain verification, allowlist, weighting
   states: if it guards trust, it ships with a failing-then-passing test
   under `rust/` (`cargo test`).
4. Keep the desktop stack in Rust (`rust/crates/*`). Optional adapters may
   use the host editor’s language, but they only append allowlisted counts.
5. Keep docs and UI copy plain and direct. No buzzword filler.

Good first work: a new adapter under `adapters/`, calibration notes in
`weighting/`, or trying to fake hours against your own server and reporting how.
