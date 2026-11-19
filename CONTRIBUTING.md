# Contributing

```bash
make check    # before anything, and before you open a pr
```

rules of the repo, in order:

1. **privacy is the product.** a change that lets raw text, paths,
   prompts, commands or pixels reach the server is rejected on sight,
   including in tests and logs. the allowlist test is not negotiable.
2. **weights are versioned, never edited.** changing the formula means
   a new `formula_version` and calibration notes in `weighting/`, not
   quietly different numbers under the same version.
3. **tests first for trust paths.** chain verification, allowlist,
   weighting states, integration signatures: if it guards trust, it
   ships with a failing-then-passing test.
4. **stdlib only** in `server/`, `daemon/` and `src/` unless a change
   explains why a dependency earns its keep on a 10-year-old laptop.
5. keep it boring. plain tables, plain files, plain language in docs.

good first work: a new harness adapter (see `adapters/` — the contract
is "append counts to the exec log, throw content away"), calibration
sessions per `weighting/calibration-20.md`, and trying to fake hours
against your own server and reporting how.
