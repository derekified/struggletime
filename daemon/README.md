# Desktop tracker (native)

Hooks and installers for the Rust binaries built from `rust/`.

| File | Role |
|---|---|
| `install.sh` / `install.ps1` | Build & link `struggletime-*` binaries |
| `hooks.sh` / `hooks.ps1` | Shell exec categories (no argv) |
| `autostart.sh` | Login autostart helper |
| `assets/` | Tray / brand SVG |

Commands:

```bash
make rust && make install-binaries
struggletime-helper apikey <st_…>
struggletime-helper start --repo ~/my-project
struggletime-tray
```
