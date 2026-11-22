PY ?= python3
PORT ?= 18765

.PHONY: help check test test-cross verify-adapters server daemon gui tray inspect demo stress-smoke stress package calibrate sync-docs docs docs-dev clean
help:
	@echo "make check    verify python, git and the test suite"
	@echo "make test     run all tests"
	@echo "make test-cross  Linux/macOS/Windows path+hook+adapter tests"
	@echo "make verify-adapters  privacy + emit contract across all adapters"
	@echo "make server   start the server + dashboard on :$(PORT)"
	@echo "make tray     run the menu bar / system tray daemon"
	@echo "make gui      open the daemon settings window"
	@echo "make daemon   track a repo:  make daemon REPO=~/my-project USER=you"
	@echo "make demo     start a server with demo data on :18766"
	@echo "make inspect  show what a repo would send:  make inspect REPO=~/my-project"
	@echo "make package  build dist/struggletime-*.tar.gz"
	@echo "make calibrate run 20-session calibration report"
	@echo "make sync-docs assert YAML/schema match verify.py"
	@echo "make docs     build Fumadocs static site into docs-site/out"
	@echo "make docs-dev run Fumadocs next dev"
	@echo "make stress-smoke  acceptance smoke: 5 weighted projects"
	@echo "make stress        acceptance: 1000 weighted projects (ephemeral server)"

check:
	$(PY) scripts/check.py

test:
	$(PY) -m unittest discover -s tests
	$(PY) scripts/sync_formula_docs.py
	$(PY) scripts/verify_adapters.py

test-cross:
	PWSH="$(PWSH)" $(PY) -m unittest tests.test_cross_platform -v

verify-adapters:
	$(PY) scripts/verify_adapters.py

server:
	PORT=$(PORT) $(PY) server/server.py

tray:
	$(PY) daemon/tray.py

gui:
	$(PY) daemon/gui.py

daemon:
	@test -n "$(REPO)" || (echo "set REPO=path/to/repo" && exit 1)
	AITIME_EXEC_LOG=$$HOME/.aitime/exec.jsonl $(PY) daemon/daemon.py --repo "$(REPO)" --server http://127.0.0.1:$(PORT) --user "$(USER)"

inspect:
	@test -n "$(REPO)" || (echo "set REPO=path/to/repo" && exit 1)
	$(PY) daemon/tracker_inspect.py "$(REPO)"

demo:
	PORT=18766 SERVER_DB=/tmp/struggletime-demo.db $(PY) scripts/demo.py

stress-smoke:
	$(PY) daemon/stress_test.py --smoke --cleanup-after

stress:
	$(PY) daemon/stress_test.py --count 1000 --cleanup-after

package:
	sh scripts/package.sh

calibrate:
	$(PY) scripts/run_calibration_20.py

sync-docs:
	$(PY) scripts/sync_formula_docs.py

docs:
	cd docs-site && npm run build

docs-dev:
	cd docs-site && npm run dev

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; true
