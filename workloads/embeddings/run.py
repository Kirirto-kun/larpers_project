"""Offline E5 map/index, trusted reference generation, and local search."""
from __future__ import annotations

import argparse
import json
import sys

from workloads.common import read_json, write_json
from .adapter import aggregate, create_reference, run, search, validate_input, verify


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", default="/outputs/index.json")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--microbatch-size", type=int, default=32)
    parser.add_argument("--task", help="a TaskSpec emitted by split; its manifest binding is checked")
    parser.add_argument("--reference", help="trusted validator-owned reference; do not expose to workers")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--make-reference", metavar="FILE")
    mode.add_argument("--search", metavar="INDEX")
    parser.add_argument("--reference-device-id", default="local-reference")
    parser.add_argument("--query")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)
    if args.query and not args.search:
        parser.error("--query requires --search")
    try:
        manifest = read_json(args.manifest)
        if args.search:
            index = read_json(args.search, max_bytes=2 << 30)
            policy = {"mode": "full_reference", "reference_path": args.reference} if args.reference else {}
            verdict = verify(manifest, index, policy)
            if not verdict["accepted"]:
                raise ValueError("; ".join(verdict["errors"]))
            result = {"results": search(manifest, index, args.query, args.top_k, device=args.device),
                      "quality": verdict["quality"], "coverage": verdict["coverage"],
                      "checked_items": verdict["checked_items"], "benchmark_eligible": verdict["benchmark_eligible"]}
        elif args.make_reference:
            reference = create_reference(manifest, args.make_reference, device=args.device, device_id=args.reference_device_id)
            result = {"reference_path": args.make_reference, "manifest_digest": reference["manifest_digest"],
                      "reference_device_id": reference["reference_device_id"],
                      "reference_duration_ms": reference["reference_duration_ms"], "units": len(reference["vectors"])}
        else:
            ids = None
            if args.task:
                task = read_json(args.task)
                if task.get("manifest_digest") != validate_input(manifest)["manifest_digest"]:
                    raise ValueError("task manifest binding mismatch")
                ids = task["item_ids"]
            output = run(manifest, {"microbatch_size": args.microbatch_size}, device=args.device, item_ids=ids)
            policy = {"mode": "full_reference", "reference_path": args.reference} if args.reference else {}
            if ids is None:
                artifact = aggregate({"manifest": manifest, "outputs": [output], "policy": policy})
                verdict = artifact["validation"]
            else:
                artifact = output
                verdict = verify(manifest, output, {**policy, "item_ids": ids})
                if not verdict["accepted"]:
                    raise ValueError("; ".join(verdict["errors"]))
            write_json(args.output, artifact)
            result = {"output_path": args.output, "units": len(output["vectors"]),
                      "manifest_digest": output["manifest_digest"], "stats": output["stats"],
                      "quality": verdict["quality"], "benchmark_eligible": verdict["benchmark_eligible"]}
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
