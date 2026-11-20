#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end aitime daemon smoke: project + human/agent bursts + hours."""
import hashlib, json, os, pathlib, secrets, sqlite3, subprocess, sys, tempfile, time, urllib.error, urllib.request, uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "daemon"))
from verify import GENESIS, bucket_hash, weight_sequence

def req(port, method, path, body=None, token=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")

def struggle_bucket(project, prev, start, secs, counts):
    b = {
        "v": 1, "bucket_id": str(uuid.uuid4()), "install_id": "aitime-smoke",
        "project_id": project, "repo_remote_hash": None,
        "bucket_start_wall": start, "bucket_secs": secs, "mono_delta_ms": secs * 1000,
        "counts": counts,
        "git": {"head_commit": "deadbeef", "lines_added": 20, "lines_deleted": 4, "dirty_files": 2},
        "files": [], "formula_version": "v0.1.0-alpha", "prev_hash": prev,
    }
    b["hash"] = bucket_hash(b)
    return b

def run_daemon_with_edits(env, repo, project, port, token, edit_fn, bucket_secs=2):
    """Start daemon, apply edits during the bucket window, wait for --once seal."""
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "daemon" / "daemon.py"),
         "--repo", str(repo), "--project", project,
         "--server", f"http://127.0.0.1:{port}", "--user", "smoke", "--token", token,
         "--bucket-secs", str(bucket_secs), "--once"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    time.sleep(0.4)
    edit_fn()
    out, _ = proc.communicate(timeout=45)
    return proc.returncode, out or ""

def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="st-aitime-smoke-"))
    db = tmp / "smoke.db"
    port = 18978
    home = tmp / "aitime"
    home.mkdir()
    env = {
        **os.environ,
        "PORT": str(port), "SERVER_DB": str(db), "SLACK_WEBHOOK_URL": "",
        "STRUGGLETIME_HOME": str(home), "AITIME_HOME": str(home),
        "AITIME_EXEC_LOG": str(home / "exec.jsonl"),
    }
    # ensure no wakatime path is required
    env.pop("WAKATIME_HOME", None)

    proc = subprocess.Popen([sys.executable, str(ROOT / "server" / "server.py")], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health"); break
            except Exception:
                time.sleep(0.1)
        else:
            raise SystemExit("server failed to start")

        token = secrets.token_urlsafe(16)
        th = hashlib.sha256(token.encode()).hexdigest()
        now = int(time.time())
        c = sqlite3.connect(db)
        c.execute("INSERT INTO user(handle,token_hash,is_public,created_at) VALUES (?,?,?,?)", ("smoke", "", 1, now))
        c.execute("INSERT INTO link(handle,github_login,slack_user_id,github_token,slack_avatar,slack_name) VALUES (?,?,?,?,?,?)",
                  ("smoke", None, "Usmoke", None, None, "smoke"))
        c.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?)", (th, "smoke", "Usmoke", "", "", now, now + 86400))
        c.commit(); c.close()

        # --- create real project ---
        s, p = req(port, "POST", "/v1/projects", {
            "id": "aitime-smoke-test", "name": "Aitime Smoke Test",
            "repo_url": "https://github.com/example/aitime-smoke-test",
        }, token=token)
        assert s == 201, p
        print("project:", p["id"])

        # --- gui.json persistence ---
        import config as dcfg
        dcfg.HOME = home
        dcfg.GUI_SETTINGS = home / "gui.json"
        dcfg.PAUSE_FLAG = home / "paused"
        dcfg.save_settings({
            "handle": "smoke", "repo": str(tmp / "aitime-smoke-test"),
            "server": f"http://127.0.0.1:{port}", "bucket_mins": 5, "token": token,
            "project": "aitime-smoke-test",
        })
        loaded = dcfg.load_settings()
        assert loaded["handle"] == "smoke" and "wakatime" not in str(loaded).lower()
        print("gui.json ok:", dcfg.GUI_SETTINGS)

        # --- git workspace ---
        repo = tmp / "aitime-smoke-test"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.t"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
        (repo / "main.py").write_text("print(1)\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)

        # shell hook style exec log (no args)
        (home / "exec.jsonl").write_text(
            json.dumps({"ts": now, "category": "test_runner", "verb": "", "exit_code": 1}) + "\n" +
            json.dumps({"ts": now, "category": "compiler", "verb": "", "exit_code": 0}) + "\n" +
            json.dumps({"ts": now, "category": "ai_cli", "verb": "prompt", "exit_code": 0}) + "\n"
        )

        # --- human burst: 1–2 files during the watch window ---
        def human_edits():
            (repo / "main.py").write_text("print('human')\n")
            (repo / "note.md").write_text("# hi\n")
        import ast, re
        code, out = run_daemon_with_edits(env, repo, "aitime-smoke-test", port, token, human_edits)
        assert code == 0, out
        human_line = [l for l in out.splitlines() if "sealed" in l][-1]
        m = re.search(r"sealed (\{.*\})", human_line)
        assert m, human_line
        hc = ast.literal_eval(m.group(1))
        assert hc["human_edits"] >= 1 and hc["ai_writes"] == 0, hc
        print("human burst:", human_line)

        # --- agentic burst: 5+ files during the watch window ---
        def agent_edits():
            for i in range(6):
                (repo / f"gen_{i}.py").write_text(f"x={i}\n")
        code, out = run_daemon_with_edits(env, repo, "aitime-smoke-test", port, token, agent_edits)
        assert code == 0, out
        agent_line = [l for l in out.splitlines() if "sealed" in l][-1]
        m = re.search(r"sealed (\{.*\})", agent_line)
        assert m, agent_line
        ac = ast.literal_eval(m.group(1))
        assert ac["ai_writes"] >= 5, ac
        print("agent burst:", agent_line)

        # --- helper lifecycle ---
        henv = {**env, "STRUGGLETIME_TOKEN": token, "STRUGGLETIME_SERVER": f"http://127.0.0.1:{port}"}
        def helper(*args):
            return subprocess.run([sys.executable, str(ROOT / "daemon" / "helper.py"), *args],
                                 env=henv, capture_output=True, text=True, timeout=45)
        assert helper("start", "--repo", str(repo), "--user", "smoke", "--project", "aitime-smoke-test",
                      "--bucket-secs", "60", "--server", f"http://127.0.0.1:{port}", "--token", token).returncode == 0
        assert "running" in helper("status").stdout
        assert helper("pause").returncode == 0
        assert helper("resume").returncode == 0
        assert helper("stop").returncode == 0
        print("helper ok")

        # --- tray headless ---
        tr = subprocess.run(
            [sys.executable, str(ROOT / "daemon" / "tray.py"), "--headless",
             "--repo", str(repo), "--user", "smoke",
             "--server", f"http://127.0.0.1:{port}", "--token", token],
            env={**env, "DISPLAY": "", "WAYLAND_DISPLAY": ""},
            input="status\nquit\n", capture_output=True, text=True, timeout=30,
        )
        assert tr.returncode == 0, tr.stdout + tr.stderr
        assert "headless" in tr.stdout.lower() or "status" in tr.stdout.lower()
        print("tray headless ok")

        # --- gui check ---
        gui = subprocess.run([sys.executable, str(ROOT / "daemon" / "gui.py"), "--check"],
                             env=env, capture_output=True, text=True, timeout=20)
        print("gui check:", gui.stdout.strip() or gui.stderr.strip() or gui.returncode)

        # --- offline queue then flush (measurable hours) ---
        ledger_path = repo / ".aitime-ledger.json"
        ledger = json.loads(ledger_path.read_text())
        prev = ledger.get("prev_hash") or GENESIS
        pending = []
        # human-weighted closed loop sessions (30 min × 4 = 2h)
        human_counts = {
            "human_edits": 8, "ai_writes": 0, "prompts": 1, "executions": 3,
            "exec_failures": 2, "commits": 1, "dwell_diff_secs": 40, "idle_secs": 0,
        }
        # agentic dump session
        agent_counts = {
            "human_edits": 0, "ai_writes": 40, "prompts": 0, "executions": 0,
            "exec_failures": 0, "commits": 0, "dwell_diff_secs": 0, "idle_secs": 0,
        }
        start = now - 9000
        for i in range(4):
            b = struggle_bucket("aitime-smoke-test", prev, start + i * 1800, 1800, human_counts)
            pending.append(b); prev = b["hash"]
        b = struggle_bucket("aitime-smoke-test", prev, start + 7200, 1800, agent_counts)
        pending.append(b); prev = b["hash"]
        # verify local continuation chain (not from GENESIS — continues ledger head)
        walk = ledger.get("prev_hash") or GENESIS
        for i, b in enumerate(pending):
            assert b["prev_hash"] == walk, f"bucket {i}: prev mismatch"
            assert bucket_hash(b) == b["hash"], f"bucket {i}: hash mismatch"
            walk = b["hash"]
        weights = weight_sequence(pending)
        print("hash-chain: ok · weights:", [(round(w, 2), st) for w, st in weights])
        ledger["pending"] = pending
        ledger["prev_hash"] = prev
        ledger_path.write_text(json.dumps(ledger, indent=2))
        # offline: stop server? instead just sync via helper (queue was local)
        sy = helper("sync", "--repo", str(repo), "--user", "smoke",
                    "--server", f"http://127.0.0.1:{port}", "--token", token)
        assert sy.returncode == 0, sy.stdout + sy.stderr
        print("flush:", sy.stdout.strip().splitlines()[-1] if sy.stdout.strip() else "ok")

        s, st = req(port, "GET", "/v1/projects/aitime-smoke-test/stats", token=token)
        assert s == 200, st
        hours = st.get("weighted_hours") or 0
        assert hours >= 1.5, f"expected measurable hours, got {hours} ({st})"
        print(f"stats: {hours}h · {st.get('buckets')} buckets")

        s, me = req(port, "GET", "/v1/me/analytics?range=all&project=aitime-smoke-test&tz_offset=0", token=token)
        assert s == 200, me
        assert (me.get("total_hours") or 0) >= 1.5
        print(f"analytics: {me['total_hours']}h · top_project={me.get('top_project')}")

        # --- adapters smoke ---
        # openclaw --once against empty sessions dir
        oc = subprocess.run(
            [sys.executable, str(ROOT / "adapters" / "openclaw" / "adapter.py"),
             "--sessions-dir", str(tmp / "no-sessions"), "--once"],
            env=env, capture_output=True, text=True, timeout=15,
        )
        assert oc.returncode == 0, oc.stderr
        # vscode extension.js parses
        vs = (ROOT / "adapters" / "vscode" / "extension.js").read_text()
        assert "AITIME_EXEC_LOG" in vs and "human_edit" in vs
        # claude hooks.json valid
        hooks = json.loads((ROOT / "adapters" / "claude-code" / "hooks.json").read_text())
        assert "hooks" in hooks and "PostToolUse" in hooks["hooks"]
        # codex readme documents wrapper
        assert "ai_cli" in (ROOT / "adapters" / "codex" / "README.md").read_text()
        print("adapters smoke ok")

        # inspect
        insp = subprocess.run([sys.executable, str(ROOT / "daemon" / "tracker_inspect.py"), str(repo)],
                              capture_output=True, text=True, timeout=20)
        print("inspect:", (insp.stdout or insp.stderr).splitlines()[0] if (insp.stdout or insp.stderr) else "empty")

        print("AITIME_SMOKE_OK")
        print(json.dumps({
            "project": "aitime-smoke-test",
            "weighted_hours": hours,
            "buckets": st.get("buckets"),
            "human_burst": "human_edits" in human_line,
            "agent_burst": "ai_writes" in agent_line,
            "gui_json": str(dcfg.GUI_SETTINGS),
        }))
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()

if __name__ == "__main__":
    sys.exit(main())
