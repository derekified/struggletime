# Privacy & ledger

See the Fumadocs reference at `/docs/privacy` (source: `docs-site/content/docs/privacy.mdx`).

Guarantees in short:

- `daemon/daemon.py` is 100% Python stdlib
- No screen, keystroke, prompt, content, or command-arg collection
- File IDs are `sha256(salt:path)`
- Allowlisted schema, ~25–50 KB metadata budgets
- Hash-chained ledger via `prev_hash` in `.aitime-ledger.json`
- Offline queue flush + server chain-head adoption
