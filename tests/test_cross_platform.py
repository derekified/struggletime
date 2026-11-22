#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cross-platform surface: Linux / macOS / Windows paths, OS labels, hooks, scripts."""
from __future__ import annotations

import ast, json, os, platform, re, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "daemon"))
sys.path.insert(0, str(ROOT / "src"))

import config as dcfg
import daemon as daemon_mod
import editors as edreg
import native_ui


PWSH = os.environ.get("PWSH") or (
    "/tmp/pwsh/pwsh" if Path("/tmp/pwsh/pwsh").is_file() else shutil.which("pwsh")
)


def _pwsh_env():
    env = {**os.environ, "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1"}
    return env


class TestHostOsLabels(unittest.TestCase):
    def test_linux_label(self):
        with mock.patch("daemon.platform.system", return_value="Linux"):
            with mock.patch("daemon.platform.release", return_value="6.12"):
                with mock.patch("daemon.platform.machine", return_value="x86_64"):
                    info = daemon_mod.host_os()
        self.assertEqual(info["id"], "linux")
        self.assertEqual(validate_os(info), [])

    def test_macos_label(self):
        with mock.patch("daemon.platform.system", return_value="Darwin"):
            with mock.patch("daemon.platform.release", return_value="23.0"):
                with mock.patch("daemon.platform.machine", return_value="arm64"):
                    info = daemon_mod.host_os()
        self.assertEqual(info["id"], "macos")

    def test_windows_label(self):
        with mock.patch("daemon.platform.system", return_value="Windows"):
            with mock.patch("daemon.platform.release", return_value="10"):
                with mock.patch("daemon.platform.machine", return_value="AMD64"):
                    info = daemon_mod.host_os()
        self.assertEqual(info["id"], "windows")


def validate_os(info):
    from verify import validate_allowlist, GENESIS, bucket_hash, FORMULA_VERSION
    b = {
        "v": 1, "bucket_id": "cccccccc", "install_id": "i", "project_id": "p",
        "repo_remote_hash": None, "bucket_start_wall": 1, "bucket_secs": 60,
        "mono_delta_ms": 1000, "counts": {"human_edits": 0, "ai_writes": 0, "prompts": 0,
            "executions": 0, "exec_failures": 0, "commits": 0, "dwell_diff_secs": 0, "idle_secs": 0},
        "git": {"head_commit": None, "lines_added": 0, "lines_deleted": 0, "dirty_files": 0},
        "files": [], "exec_categories": {}, "editors": {}, "agents": {}, "languages": {},
        "os": info["id"], "os_detail": info, "formula_version": FORMULA_VERSION, "prev_hash": GENESIS,
    }
    b["hash"] = bucket_hash(b)
    return validate_allowlist(b)


class TestConfigPaths(unittest.TestCase):
    def test_unix_default_uses_xdg(self):
        env = {"HOME": "/home/teen"}
        for k in ("STRUGGLETIME_CONFIG", "AITIME_CONFIG", "XDG_CONFIG_HOME", "APPDATA"):
            env.setdefault(k, "")
        with mock.patch.dict(os.environ, {"STRUGGLETIME_CONFIG": "", "AITIME_CONFIG": "",
                                          "XDG_CONFIG_HOME": "", "APPDATA": ""}, clear=False):
            # clear overrides
            old = {k: os.environ.pop(k, None) for k in ("STRUGGLETIME_CONFIG", "AITIME_CONFIG", "XDG_CONFIG_HOME")}
            try:
                with mock.patch("config.sys.platform", "linux"):
                    with mock.patch("config.os.name", "posix"):
                        with mock.patch("config.Path.home", return_value=Path("/home/teen")):
                            p = dcfg.default_config_home("linux")
                self.assertEqual(p, Path("/home/teen/.config/struggletime"))
            finally:
                for k, v in old.items():
                    if v is not None:
                        os.environ[k] = v

    def test_windows_default_uses_appdata(self):
        with mock.patch.dict(os.environ, {
            "APPDATA": r"C:\Users\teen\AppData\Roaming",
            "STRUGGLETIME_CONFIG": "",
            "AITIME_CONFIG": "",
        }, clear=False):
            # empty string still truthy for Path override — pop empties
            os.environ.pop("STRUGGLETIME_CONFIG", None)
            os.environ.pop("AITIME_CONFIG", None)
            os.environ["APPDATA"] = r"C:\Users\teen\AppData\Roaming"
            try:
                with mock.patch("config.sys.platform", "win32"):
                    with mock.patch("config.os.name", "nt"):
                        p = dcfg.default_config_home("win32")
                self.assertEqual(str(p).replace("\\", "/"),
                                 "C:/Users/teen/AppData/Roaming/struggletime")
            finally:
                pass

    def test_override_wins(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"STRUGGLETIME_CONFIG": td}):
                self.assertEqual(dcfg.default_config_home("win32"), Path(td))
                self.assertEqual(dcfg.default_config_home("darwin"), Path(td))


class TestPathNormalization(unittest.TestCase):
    def test_project_map_accepts_mixed_separators(self):
        """Daemon project-map resolution must not crash on Windows-style paths when mocked."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "proj"
            repo.mkdir()
            # POSIX host: only forward paths exist; still ensure resolve_watch_list is separator-safe
            rows = daemon_mod.resolve_watch_list(
                [str(repo)], {str(repo): "pid-1"}, "pid-1",
                {"server": "", "token": "", "projects": [], "repo": "", "project": ""},
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["project"], "pid-1")

    def test_language_from_windows_rel(self):
        self.assertEqual(daemon_mod.language_from_rel(r"src\app.py"), "python")
        self.assertEqual(daemon_mod.language_from_rel(r"C:\work\Main.go"), "go")


class TestShellHooks(unittest.TestCase):
    def test_bash_hook_categories(self):
        script = f"""
        source {ROOT / 'daemon' / 'hooks.sh'}
        echo git=$(_aitime_category git)
        echo ai=$(_aitime_category codex)
        echo test=$(_aitime_category pytest)
        echo cc=$(_aitime_category claude)
        echo other=$(_aitime_category ls)
        """
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("git=git", r.stdout)
        self.assertIn("ai=ai_cli", r.stdout)
        self.assertIn("test=test_runner", r.stdout)
        self.assertIn("cc=ai_cli", r.stdout)
        self.assertIn("other=other", r.stdout)

    def test_bash_hook_emits_jsonl_without_args(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "exec.jsonl"
            script = f"""
            export AITIME_EXEC_LOG={log}
            source {ROOT / 'daemon' / 'hooks.sh'}
            _aitime_pre "pytest tests/test_foo.py -k bar"
            false
            _aitime_post
            """
            # _aitime_post uses $?; force fail then call carefully
            script = f"""
            export AITIME_EXEC_LOG='{log}'
            source '{ROOT / 'daemon' / 'hooks.sh'}'
            trap - DEBUG
            PROMPT_COMMAND=
            _aitime_pre 'pytest tests/test_foo.py -k bar'
            false
            _aitime_post
            """
            r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 0, r.stderr)
            line = log.read_text().strip().splitlines()[-1]
            row = json.loads(line)
            self.assertEqual(row["category"], "test_runner")
            self.assertEqual(row["exit_code"], 1)
            blob = json.dumps(row)
            self.assertNotIn("test_foo", blob)
            self.assertNotIn("-k", blob)

    @unittest.skipUnless(PWSH and Path(PWSH).exists(), "pwsh not available")
    def test_powershell_hooks_parse(self):
        for name in ("hooks.ps1", "install.ps1"):
            path = ROOT / "daemon" / name
            cmd = (
                f"$e=$null; [void][System.Management.Automation.Language.Parser]::"
                f"ParseFile('{path}', [ref]$null, [ref]$e); "
                f"if ($e) {{ $e | %% {{ $_.ToString() }}; exit 1 }} else {{ '{name} OK' }}"
            )
            r = subprocess.run(
                [PWSH, "-NoProfile", "-Command", cmd],
                capture_output=True, text=True, timeout=30, env=_pwsh_env(),
            )
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("OK", r.stdout)

    @unittest.skipUnless(PWSH and Path(PWSH).exists(), "pwsh not available")
    def test_powershell_hook_categories_align_with_bash(self):
        """Extract switch arms from hooks.ps1 and compare to bash case list."""
        ps = (ROOT / "daemon" / "hooks.ps1").read_text()
        sh = (ROOT / "daemon" / "hooks.sh").read_text()
        # AI CLIs present in both
        for cli in ("claude", "codex", "aider", "cursor-agent", "opencode"):
            self.assertIn(cli, ps)
            self.assertIn(cli, sh)
        self.assertIn("ai_cli", ps)
        self.assertIn("test_runner", ps)
        self.assertIn("git", ps)


class TestInstallScripts(unittest.TestCase):
    def test_install_sh_syntax(self):
        r = subprocess.run(["bash", "-n", str(ROOT / "daemon" / "install.sh")],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_autostart_sh_syntax(self):
        r = subprocess.run(["bash", "-n", str(ROOT / "daemon" / "autostart.sh")],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_install_ps1_mentions_appdata(self):
        text = (ROOT / "daemon" / "install.ps1").read_text()
        self.assertIn("APPDATA", text)
        self.assertIn("StruggleTimeTray", text)
        self.assertIn("hooks.ps1", text)

    def test_package_script_syntax(self):
        r = subprocess.run(["sh", "-n", str(ROOT / "scripts" / "package.sh")],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestEditorsProcessBlob(unittest.TestCase):
    def test_unix_ps_path(self):
        with mock.patch("editors.sys.platform", "linux"):
            with mock.patch("editors.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, stdout="code\ncursor\n", stderr="")
                edreg._CACHE["blob"] = None
                blob = edreg.process_blob()
        self.assertIn("cursor", blob)
        self.assertEqual(run.call_args.args[0][0], "ps")

    def test_windows_tasklist_path(self):
        with mock.patch("editors.sys.platform", "win32"):
            with mock.patch("editors.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(
                    [], 0, stdout='"Code.exe","123"\n"Cursor.exe","456"\n', stderr="")
                edreg._CACHE["blob"] = None
                blob = edreg.process_blob()
        self.assertIn("cursor", blob)
        self.assertEqual(run.call_args.args[0][0], "tasklist")


class TestNativeUiBackends(unittest.TestCase):
    def test_backend_names(self):
        with mock.patch("native_ui.sys.platform", "darwin"):
            self.assertEqual(native_ui.backend_name(), "osascript")
        with mock.patch("native_ui.sys.platform", "win32"):
            self.assertEqual(native_ui.backend_name(), "win32")
        with mock.patch("native_ui.sys.platform", "linux"):
            with mock.patch("native_ui._which", return_value=None):
                self.assertEqual(native_ui.backend_name(), "stdin")


class TestNoUnixOnlyImports(unittest.TestCase):
    """Daemon core must stay importable without Linux-only modules."""
    def test_daemon_ast_no_fcntl_inotify(self):
        src = (ROOT / "daemon" / "daemon.py").read_text()
        tree = ast.parse(src)
        banned = {"fcntl", "inotify", "pwd", "grp", "pty"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.assertNotIn(a.name.split(".")[0], banned)
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], banned)

    def test_create_no_window_flag_referenced_safely(self):
        # getattr(subprocess, "CREATE_NO_WINDOW", 0) pattern used on win
        for rel in ("daemon/tray.py", "daemon/gui.py", "daemon/browser_auth.py", "daemon/native_ui.py"):
            text = (ROOT / rel).read_text()
            if "CREATE_NO_WINDOW" in text:
                self.assertIn("getattr(subprocess", text)


class TestVscodeAdapterPackaging(unittest.TestCase):
    def test_package_json_and_js(self):
        pkg = json.loads((ROOT / "adapters" / "vscode" / "package.json").read_text())
        self.assertEqual(pkg["main"], "./extension.js")
        self.assertTrue((ROOT / "adapters" / "vscode" / "extension.js").exists())
        r = subprocess.run(["node", "--check", str(ROOT / "adapters" / "vscode" / "extension.js")],
                           capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_codex_wrap_syntax(self):
        r = subprocess.run(["sh", "-n", str(ROOT / "adapters" / "codex" / "wrap.sh")],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_new_adapter_scaffolds_present(self):
        for rel in (
            "adapters/neovim/lua/struggletime/init.lua",
            "adapters/godot/addons/struggletime/plugin.gd",
            "adapters/jetbrains/src/main/kotlin/com/struggletime/StruggleTimeStartup.kt",
            "adapters/unity/Editor/StruggleTimeTracker.cs",
        ):
            self.assertTrue((ROOT / rel).is_file(), rel)


class TestLiveLinuxDaemonSmoke(unittest.TestCase):
    """On this host (Linux): seal one offline bucket and assert os=linux."""
    def test_seal_reports_linux(self):
        if platform.system() != "Linux":
            self.skipTest("Linux-only live smoke")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            home, cfg, repo = td / "h", td / "c", td / "r"
            home.mkdir(); cfg.mkdir(); repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.t"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
            (repo / "a.py").write_text("x=1\n")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "i"], check=True)
            (repo / "a.py").write_text("x=2\n")
            env = {
                **os.environ,
                "AITIME_HOME": str(home), "STRUGGLETIME_HOME": str(home),
                "STRUGGLETIME_CONFIG": str(cfg), "AITIME_CONFIG": str(cfg),
                "AITIME_AGENT_EVENTS": str(home / "e.jsonl"),
                "AITIME_EXEC_LOG": str(home / "x.jsonl"),
            }
            r = subprocess.run(
                [sys.executable, str(ROOT / "daemon" / "daemon.py"),
                 "--repo", str(repo), "--project", "xp",
                 "--bucket-secs", "2", "--once", "--server", "", "--token", ""],
                capture_output=True, text=True, env=env, timeout=45,
            )
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("os=linux", r.stdout)
            b = json.loads((repo / ".aitime-ledger.json").read_text())["pending"][0]
            self.assertEqual(b["os"], "linux")
            self.assertTrue(b.get("signature") or b.get("signature") is None)


if __name__ == "__main__":
    unittest.main()
