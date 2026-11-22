# StruggleTime

Time tracking that counts **productive struggle**, not raw activity.
Built for Hack Club builders shipping open source. Reviewers see structure
and weights — not screenshots or prompts. AGPLv3.

Pitch: [`PITCH.md`](PITCH.md) · Docs: `/docs` after `make docs`

An unattended agent hour is not a learning hour. StruggleTime watches the
loop `(edit|prompt) → (test|build) → (follow-up|commit)`, weights it, and
hash-chains it so quiet rewrites are obvious.

## Run it

```bash
make check
make server                                  # :18765
sh daemon/install.sh                         # once per machine
struggletime-helper apikey <st_…>            # from Settings
struggletime-helper start --repo ~/my-project
struggletime-helper status
```

Open http://127.0.0.1:18765. Python 3.10+ stdlib for core. Demo seed:
`make demo` on :18766.

## Repo layout

| Path | What |
|---|---|
| `daemon/` | Stdlib tracker, helper CLI, tray, installers, hooks |
| `server/` | HTTP API + dashboard SPA (`server/static/`) |
| `src/` | Shared `verify.py` formula |
| `weighting/` | Rules YAML + calibration notes |
| `docs-site/` | Fumadocs source → `docs-site/out` (`make docs`) |
| `docs/` | Short markdown mirrors / pointers |
| `schemas/` | Allowlist + SQL |
| `scripts/` | Demo, package, formula sync |
| `adapters/` | Optional IDE/agent helpers |
| `tests/` | Unittest suite (`make check`) |

## Docs

Fumadocs at `/docs` (`make docs`). Plain mirrors under `docs/`.

## License

AGPLv3 — see `LICENSE`.
