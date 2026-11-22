PORT ?= 18765

.PHONY: help check test verify-adapters adversarial server daemon tray rust rust-linux rust-test binaries install install-binaries docs docs-dev package clean clippy

help:
	@echo "make rust / binaries   build unified struggletime binary"
	@echo "make rust-linux        same + AppIndicator tray (--features linux-tray)"
	@echo "make install           App + CLI (macOS/Linux via scripts/install.sh)"
	@echo "make check             cargo test + adapter verify"
	@echo "make adversarial       isolated subagent adversarial harness"
	@echo "make clippy            cargo clippy -D warnings"
	@echo "make server            struggletime serve on :$(PORT)"
	@echo "make daemon            struggletime daemon --repo=\$$REPO"
	@echo "make tray              struggletime --tray"
	@echo "make docs              build Fumadocs static site"
	@echo "make package           source tarball (scripts/package.sh)"
	@echo ""
	@echo "Linux tray build deps (Debian/Ubuntu):"
	@echo "  sudo apt-get install -y libxdo-dev libgtk-3-dev libayatana-appindicator3-dev pkg-config"

check:
	sh scripts/check.sh

test: check

verify-adapters:
	sh scripts/verify_adapters.sh

adversarial: rust
	bash scripts/adversarial_subagent_harness.sh

rust binaries:
	cd rust && cargo build --release -p struggletime

# AppIndicator tray pulls GTK + libxdo + Ayatana (see help for apt packages).
rust-linux:
	cd rust && cargo build --release -p struggletime --features linux-tray

rust-test:
	cd rust && cargo test --workspace

clippy:
	cd rust && cargo clippy --workspace --all-targets -- -D warnings

install install-binaries: rust
	sh scripts/install.sh

server: rust
	PORT=$(PORT) ./rust/target/release/struggletime serve

daemon: rust
	@test -n "$(REPO)" || (echo "set REPO=path/to/repo" && exit 1)
	./rust/target/release/struggletime daemon --repo "$(REPO)" --user "$(USER)"

tray: rust
	./rust/target/release/struggletime --tray

docs:
	cd docs-site && npm run build

docs-dev:
	cd docs-site && npm run dev

package:
	sh scripts/package.sh

clean:
	cd rust && cargo clean
	rm -rf docs-site/.next docs-site/out
