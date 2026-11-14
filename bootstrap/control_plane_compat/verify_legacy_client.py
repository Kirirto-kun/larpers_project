"""Verify the existing Go client against a sanitized server integration fixture."""
import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--fixture", type=Path, required=True)
parser.add_argument("--go", default="go")
args = parser.parse_args()
fixture = args.fixture.resolve(strict=True)
directory = ROOT / ".git/campus-runner/mac-compute-evidence"
directory.mkdir(parents=True, exist_ok=True)
overlay = directory / "legacy-client-overlay.json"
overlay.write_text(json.dumps({"Replace": {
    str(ROOT / "runner/internal/client/diagnostic_compat_overlay_test.go"):
        str(Path(__file__).with_name("legacy_client_test.go"))}}))
env = dict(os.environ)
env["CAMPUS_DIAGNOSTIC_FIXTURE"] = str(fixture)
raise SystemExit(subprocess.run([
    args.go, "test", "-count=1", "-overlay="+str(overlay), "./internal/client",
    "-run", "TestDiagnosticRichServerFixtureAcceptedByExistingClient", "-v"],
    cwd=ROOT/"runner", env=env).returncode)
