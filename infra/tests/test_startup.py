"""Startup boundary checks; fake npm/Docker, never contact the live deployment."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh")
BASH = ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash"))


class StartupTests(unittest.TestCase):
    def run_start(self, shell, runtime=False, auto=False, incomplete=False, fail="", project="", fresh_env=True, packaged=False):
        with tempfile.TemporaryDirectory(prefix="campus-start-") as temporary:
            root = Path(temporary)
            (root / "infra").mkdir()
            for app in ("portal", "console"):
                (root / "apps" / app).mkdir(parents=True)
            shutil.copy(ROOT / "infra" / f"start.{shell}", root / "infra")
            if fresh_env:
                (root / ".env").touch()
            log = root / "calls.log"
            env = os.environ.copy()
            env.pop("CAMPUS_RUNTIME_DIR", None)
            env.pop("COMPOSE_PROJECT_NAME", None)
            env.pop("CAMPUS_PROJECT_NAME", None)
            env["LOCALAPPDATA"] = str(root / "local")
            env["CALL_LOG"] = str(log)
            env["FAIL_STEP"] = fail
            private = root / "local" / "Campus" / "server-96bfe6d" if auto else root / "private runtime"
            if packaged:
                private = root / "local" / "Packages" / "OpenAI.Codex_test" / "LocalCache" / "Local" / "Campus" / "server-96bfe6d"
            if runtime or auto or incomplete or packaged:
                private.mkdir(parents=True)
                (private / "runtime.env").touch()
                if not incomplete:
                    (private / "live.override.yaml").touch()
                if not auto and not packaged:
                    env["CAMPUS_RUNTIME_DIR"] = str(private)
            if shell == "ps1":
                if not PWSH:
                    self.skipTest("PowerShell unavailable")
                driver = root / "driver.ps1"
                driver.write_text('''
function global:npm.cmd {
    Add-Content -LiteralPath $env:CALL_LOG -Value ("npm|$PWD|" + ($args -join '|'))
    $global:LASTEXITCODE = if ($env:FAIL_STEP -eq ($args -join ' ')) { 7 } else { 0 }
}
function global:npm { npm.cmd @args }
function global:docker {
    Add-Content -LiteralPath $env:CALL_LOG -Value ("docker|$PWD|" + ($args -join '|'))
    $global:LASTEXITCODE = if ($env:FAIL_STEP -eq 'docker') { 9 } else { 0 }
}
& "$PSScriptRoot/infra/start.ps1"
''', encoding="utf-8")
                command = [PWSH, "-NoProfile", "-NonInteractive", "-File", str(driver)]
                if project:
                    driver.write_text(driver.read_text(encoding="utf-8").replace(
                        '& "$PSScriptRoot/infra/start.ps1"',
                        f'& "$PSScriptRoot/infra/start.ps1" -ProjectName \'{project}\''), encoding="utf-8")
            else:
                if not BASH or not Path(BASH).exists():
                    self.skipTest("Bash unavailable")
                if "CAMPUS_RUNTIME_DIR" in env:
                    env["CAMPUS_RUNTIME_DIR"] = private.as_posix()
                bindir = root / "bin"
                bindir.mkdir()
                for program in ("npm", "docker"):
                    fake = bindir / program
                    fake.write_text(
                        '#!/usr/bin/env bash\n'
                        f'printf "{program}|%s" "$PWD" >> "$CALL_LOG"\n'
                        'printf "|%s" "$@" >> "$CALL_LOG"\n'
                        'printf "\\n" >> "$CALL_LOG"\n'
                        f'if [[ "$FAIL_STEP" == "$*" || "$FAIL_STEP" == "{program}" ]]; then exit 7; fi\n'
                        'exit 0\n', encoding="utf-8")
                    fake.chmod(0o755)
                env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
                if project:
                    env["CAMPUS_PROJECT_NAME"] = project
                command = [BASH, str(root / "infra" / "start.sh")]
            result = subprocess.run(command, check=False, env=env, capture_output=True, text=True)
            calls = log.read_text(encoding="utf-8-sig").splitlines() if log.exists() else []
            return result, calls, str(private)

    def test_fresh_builds_both_apps_before_compose(self):
        for shell in ("ps1", "sh"):
            with self.subTest(shell=shell):
                result, calls, _ = self.run_start(shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(calls), 5, calls)
                self.assertTrue(calls[0].endswith("|ci"), calls)
                self.assertIn("portal", calls[0])
                self.assertTrue(calls[1].endswith("|run|build"), calls)
                self.assertIn("console", calls[2])
                self.assertTrue(calls[2].endswith("|ci"), calls)
                self.assertTrue(calls[3].endswith("|run|build"), calls)
                self.assertIn("docker|", calls[4])
                self.assertNotIn("--project-name", calls[4])
                self.assertIn("up|-d|--build|--wait", calls[4])

    def test_explicit_runtime_retains_live_project(self):
        for shell in ("ps1", "sh"):
            with self.subTest(shell=shell):
                result, calls, _ = self.run_start(shell, runtime=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("runtime.env", calls[-1])
                self.assertIn("live.override.yaml", calls[-1])
                self.assertIn("compose.yaml", calls[-1])
                self.assertIn("--project-name|campus-live", calls[-1])

    def test_windows_discovers_existing_private_runtime(self):
        result, calls, _ = self.run_start("ps1", auto=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--project-name|campus-live", calls[-1])

    def test_windows_discovers_packaged_private_runtime(self):
        result, calls, private = self.run_start("ps1", packaged=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--project-name|campus-live", calls[-1])
        self.assertIn(str(Path(private) / "runtime.env"), calls[-1])

    def test_incomplete_packaged_runtime_does_not_create_new_pool(self):
        result, calls, _ = self.run_start("ps1", packaged=True, incomplete=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_explicit_project_override(self):
        for shell in ("ps1", "sh"):
            with self.subTest(shell=shell):
                result, calls, _ = self.run_start(shell, runtime=True, project="other-established-pool")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--project-name|other-established-pool", calls[-1])

    def test_missing_fresh_env_stops_before_build(self):
        for shell in ("ps1", "sh"):
            with self.subTest(shell=shell):
                result, calls, _ = self.run_start(shell, fresh_env=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])

    def test_incomplete_discovered_runtime_does_not_fall_back(self):
        result, calls, _ = self.run_start("ps1", auto=True, incomplete=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_incomplete_runtime_stops_without_falling_back(self):
        for shell in ("ps1", "sh"):
            with self.subTest(shell=shell):
                result, calls, _ = self.run_start(shell, incomplete=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])

    def test_command_failure_propagates_and_stops(self):
        for shell in ("ps1", "sh"):
            for step, count in (("ci", 1), ("run build", 2), ("docker", 5)):
                with self.subTest(shell=shell, step=step):
                    result, calls, _ = self.run_start(shell, fail=step)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(len(calls), count, calls)


if __name__ == "__main__":
    unittest.main()
