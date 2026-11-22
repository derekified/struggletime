# StruggleTime

Time tracking for people who ship. It gives more weight to the loops where
something breaks, you dig in, and it works again — and less to idle staring
or huge unread AI pastes. Reviewers see structure and weights, not your
screenshots or prompts.

Built for Hack Club builders. AGPLv3.

Source: [github.com/derekified/struggletime](https://github.com/derekified/struggletime)

## Quick start

```bash
git clone https://github.com/derekified/struggletime.git
cd struggletime
make check
make server                                  # dashboard on :18765
sh daemon/install.sh                         # once per machine
struggletime-helper apikey <st_…>            # from Settings
struggletime-helper start --repo ~/my-project
struggletime-helper status
```

Open http://127.0.0.1:18765. Core server and daemon need Python 3.10+ (stdlib
only). Demo data: `make demo` on :18766.

Docs site: `make docs`, then open `/docs/` on the running server.

## How it works (short)

The desktop tracker watches saved files and git activity in your project
folder. Paths are scrambled with a secret salt before upload
(`sha256(salt:path)`). Each sealed time slice links to the previous one
(`prev_hash`) so quiet edits to past history are easy to spot. Scoring rules
live in `src/verify.py` and are shared by the daemon and the server.

## Repo layout

| Path | What |
|---|---|
| `daemon/` | Tracker, helper CLI, tray, installers, shell hooks |
| `adapters/` | Optional IDE / agent helpers (VS Code, Neovim, Godot, …) |
| `server/` | HTTP API + dashboard SPA (`server/static/`) |
| `src/` | Shared scoring (`verify.py`) |
| `weighting/` | Rules YAML and calibration notes |
| `docs-site/` | Fumadocs source → static export (`make docs`) |
| `docs/` | Short plain-markdown notes (mirrors / pointers) |
| `schemas/` | Allowlist + SQL |
| `scripts/` | Demo, package, formula sync, adapter verify |
| `tests/` | Unittest suite (`make check`) |
| `examples/` | Small examples |
| `struggletime.svg` | Brand mark |

## Useful make targets

```bash
make check             # deps + tests + adapter verify
make server            # API + dashboard
make docs              # build /docs
make tray              # menu bar / system tray
make verify-adapters   # adapter emit/drain smoke checks
```

## License

AGPLv3 — see `LICENSE`.
