"""Offline regressions for the physical-device project benchmark driver."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1] / "project_benchmark.py"


class BenchmarkDriverTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location("project_benchmark", SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_dry_run_does_not_require_or_create_any_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "absent-output"
            argv = [sys.executable, str(SOURCE)]
            for flag in ("binary", "project", "state-dir", "owner-runtime", "reference"):
                argv.extend(["--" + flag, str(root / ("absent-" + flag))])
            argv.extend(["--endpoint", "https://example.invalid:8443", "--out", str(output)])
            run = subprocess.run(argv, capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertFalse(json.loads(run.stdout)["execute"])
            self.assertEqual(list(root.iterdir()), [])

    def test_submission_parser_requires_one_plain_uuid(self):
        module = self.module()
        job = "11111111-1111-4111-8111-111111111111"
        self.assertEqual(module.parse_job_id("inventory\n" + job + "\n"), job)
        for value in ("no receipt", job + "\n" + job):
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.parse_job_id(value)

    def test_completed_job_rejects_missing_unverified_and_foreign_tasks(self):
        module = self.module()
        stages = {"fit": 1, "model": 1, "evaluate": 1, "aggregate": 1}
        tasks = [
            {"id": str(index), "stage_id": stage, "node_id": module.CPU_NODE,
             "state": "succeeded", "verification_level": "structural", "attempt_id": str(index), "units": 1}
            for index, stage in enumerate(stages)
        ]
        job = {"state": "succeeded", "tasks": tasks, "failed_units": 0, "accepted_units": 4, "total_units": 4}
        module.validate_completed(job, {module.CPU_NODE}, stages)
        for change in ("missing", "unverified", "foreign", "partial"):
            invalid = json.loads(json.dumps(job))
            if change == "missing":
                invalid["tasks"].pop()
            elif change == "unverified":
                invalid["tasks"][0]["verification_level"] = None
            elif change == "foreign":
                invalid["tasks"][0]["node_id"] = module.RTX_NODE
            else:
                invalid["state"] = "partial"
            with self.subTest(change=change), self.assertRaises(ValueError):
                module.validate_completed(invalid, {module.CPU_NODE}, stages)

    def test_service_window_rates_use_observed_correct_predictions(self):
        module = self.module()
        phases = [
            {"name": "cpu", "correct_predictions": 18, "elapsed_seconds": 10},
            {"name": "rtx", "correct_predictions": 18, "elapsed_seconds": 5},
            {"name": "pool", "correct_predictions": 18, "elapsed_seconds": 3},
        ]
        report = module.comparison(phases)
        self.assertAlmostEqual(report["cpu_correct_predictions_per_second"], 1.8)
        self.assertAlmostEqual(report["pool_correct_predictions_per_second"], 6)
        self.assertAlmostEqual(report["efficiency"], 6 / 5.4)
        self.assertAlmostEqual(report["speedup"], 6 / 3.6)

    def test_api_rejects_http_before_reading_ca(self):
        with self.assertRaises(ValueError):
            self.module().Api("http://example.invalid", "secret", Path("absent-ca"))

    def test_output_lookup_supports_namespaced_model_and_root_result(self):
        module = self.module()
        artifacts = [{"name": "stages/model/uuid/model.json"}, {"name": "result.json"}]
        self.assertEqual(module.find_artifact(artifacts, "model.json")["name"], "stages/model/uuid/model.json")
        self.assertEqual(module.find_artifact(artifacts, "result.json")["name"], "result.json")
        with self.assertRaises(ValueError):
            module.find_artifact(artifacts + [{"name": "model.json"}], "model.json")

    def test_source_inventory_rejects_a_size_changed_after_cli_snapshot(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary).resolve()
            (project / "manifest.json").write_text("{}", encoding="utf-8")
            stdout = "  code manifest.json (3 bytes)\n  data dataset/a.npz (1 bytes, sha256 " + "a" * 64 + ")"
            with self.assertRaises(ValueError):
                module.inventory_identity(stdout, project)

    def test_inventory_must_match_every_bound_manifest_input(self):
        module = self.module()
        manifest = {"shards": [{"path": "dataset/a.npz", "sha256": "a" * 64}]}
        valid = {"data": [{"path": "dataset/a.npz", "sha256": "a" * 64, "size_bytes": 1}]}
        module.validate_inventory(valid, manifest)
        for data in ([], valid["data"] * 2, [{"path": "dataset/a.npz", "sha256": "b" * 64}]):
            with self.subTest(data=data), self.assertRaises(ValueError):
                module.validate_inventory({"data": data}, manifest)

    def test_child_uses_saved_login_and_direct_https_without_proxy_environment(self):
        module = self.module()
        args = SimpleNamespace(state_dir=Path("state"), endpoint="https://example.invalid")
        inherited = {
            "PATH": "keep", "CAMPUS_TOKEN": "must not inherit",
            "https_proxy": "http://proxy.invalid", "HTTP_PROXY": "http://proxy.invalid",
            "ALL_PROXY": "socks://proxy.invalid",
        }
        env = module.cli_environment(args, Path("ca.pem"), inherited)
        self.assertEqual(env["PATH"], "keep")
        self.assertEqual(env["CAMPUS_ENDPOINT"], "https://example.invalid")
        self.assertNotIn("CAMPUS_TOKEN", env)
        self.assertFalse(any(key.lower() in {"http_proxy", "https_proxy", "all_proxy"} for key in env))


if __name__ == "__main__":
    unittest.main()
