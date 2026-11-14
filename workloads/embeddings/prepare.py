"""Lock an already prepared local E5 directory and local text inputs; no download."""
import argparse
import json
from pathlib import Path
import sys

from workloads.common import file_digest, no_links, require_digest, write_json
from .adapter import validate_input


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--revision", required=True, help="trusted upstream 40-character commit SHA")
    parser.add_argument("--input", action="append", required=True, help="local UTF-8 JSONL/TXT file; may be repeated")
    parser.add_argument("--output", required=True)
    parser.add_argument("--chunk-chars", type=int, default=1000)
    parser.add_argument("--overlap-chars", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        require_digest(args.revision, 40)
        root = no_links(args.model).resolve(strict=True)
        if Path(args.output).resolve().is_relative_to(root):
            raise ValueError("write the manifest outside the immutable model directory")
        files = {path.relative_to(root).as_posix(): file_digest(path) for path in sorted(root.rglob("*")) if path.is_file()}
        manifest = {"schema_version": "1", "kind": "embeddings",
                    "model": {"id": "intfloat/multilingual-e5-small", "revision": args.revision,
                              "path": str(root), "files": files},
                    "inputs": [{"path": str(Path(value).resolve()), "format": Path(value).suffix.lstrip("."),
                                "source": Path(value).name, "sha256": file_digest(value)} for value in args.input],
                    "preprocessing": {"chunk_chars": args.chunk_chars, "overlap_chars": args.overlap_chars}}
        report = validate_input(manifest)
        write_json(args.output, manifest)
    except (ValueError, KeyError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"manifest_path": args.output, **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
