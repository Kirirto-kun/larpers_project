"""CLI for the registered trainer, suitable for the prepared CPU/CUDA profiles."""
import argparse
import json
import sys

from workloads.common import read_json
from .adapter import train


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", default="/outputs")
    parser.add_argument("--checkpoints", default="/checkpoints")
    parser.add_argument("--resume-from")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--stop-after-steps", type=int)
    args = parser.parse_args(argv)
    try:
        result = train(read_json(args.manifest), args.output, args.checkpoints,
                       resume_from=args.resume_from, device=args.device, stop_after_steps=args.stop_after_steps)
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
