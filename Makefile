PY ?= python3
PORT ?= 18765

.PHONY: help check test server daemon gui inspect demo clean
help:
	@echo "make check    verify python, git and the test suite"
	@echo "make test     run all tests"
	@echo "make server   start the server + dashboard on :$(PORT)"
	@echo "make gui      open the daemon gui"
	@echo "make daemon   track a repo:  make daemon REPO=~/my-project USER=you"
	@echo "make demo     start a server with demo data on :18766"
	@echo "make inspect  show what a repo would send:  make inspect REPO=~/my-project"

check:
	$(PY) scripts/check.py

test:
	$(PY) -m unittest discover -s tests

server:
	PORT=$(PORT) $(PY) server/server.py

gui:
	$(PY) daemon/gui.py

daemon:
	@test -n "$(REPO)" || (echo "set REPO=path/to/repo" && exit 1)
	AITIME_EXEC_LOG=$$HOME/.aitime/exec.jsonl $(PY) daemon/daemon.py --repo "$(REPO)" --server http://127.0.0.1:$(PORT) --user "$(USER)"

inspect:
	@test -n "$(REPO)" || (echo "set REPO=path/to/repo" && exit 1)
	$(PY) daemon/inspect.py "$(REPO)"

demo:
	PORT=18766 SERVER_DB=/tmp/struggletime-demo.db $(PY) scripts/demo.py

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; true
