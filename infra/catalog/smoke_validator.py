"""Trusted validator for the single-file checksum smoke workload."""

import json
import os
import re
import sys
from pathlib import Path

storage = Path(os.environ.get("CAMPUS_ADAPTER_STORAGE", "/state"))
request = json.load(sys.stdin)
data = request["payload"]
if request["operation"] == "validate_input":
    valid = len(data["inputs"]) == 1 and data["inputs"][0]["size_bytes"] <= 1_048_576
    result = {"valid": valid}
elif request["operation"] == "verify":
    expected = data["inputs"][0]["artifact"]["sha256"]
    output = data["outputs"][0]["sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", output):
        raise ValueError("Invalid artifact digest")
    with (storage / "blobs" / output[:2] / output).open("rb") as file:
        content = file.read(66)
    result = {"accepted": content == (expected + "\n").encode(), "level": "full"}
else:
    raise ValueError("Unknown operation")
json.dump({"result": result}, sys.stdout)
