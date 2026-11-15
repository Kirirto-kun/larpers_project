"""Download only the fixed public E5 revision during explicit online preparation."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

MODEL = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
FILES = ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "sentencepiece.bpe.model")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", required=True)
    args = parser.parse_args()
    root = Path(args.to)
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("model destination must not be a symlink")
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{MODEL}/revision/{REVISION}?blobs=true", timeout=30) as response:
        metadata = json.load(response)
    if metadata["sha"] != REVISION:
        raise ValueError("upstream revision mismatch")
    entries = {item["rfilename"]: item for item in metadata["siblings"]}
    manifest = {"schema_version": "1", "model_id": MODEL, "revision": REVISION, "files": {}}
    for name in FILES:
        destination = root / name
        entry = entries[name]
        expected_size = entry["size"]
        if not 0 < expected_size <= 1 << 30 or destination.is_symlink():
            raise ValueError("invalid model artifact size/path")
        if not destination.exists():
            temporary = root / (name + ".part")
            with urllib.request.urlopen(f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}", timeout=120) as source, temporary.open("wb") as out:
                received = 0
                while block := source.read(1 << 20):
                    received += len(block)
                    if received > expected_size:
                        raise ValueError("upstream artifact exceeds pinned metadata")
                    out.write(block)
            temporary.replace(destination)
        if destination.stat().st_size != expected_size:
            raise ValueError(f"model cache size mismatch: {name}")
        digest = hashlib.sha256()
        git_digest = hashlib.sha1(f"blob {expected_size}\0".encode())
        with destination.open("rb") as f:
            while block := f.read(1 << 20):
                digest.update(block)
                git_digest.update(block)
        valid = digest.hexdigest() == entry["lfs"]["sha256"] if entry.get("lfs") else git_digest.hexdigest() == entry["blobId"]
        if not valid:
            raise ValueError(f"model cache checksum mismatch: {name}")
        manifest["files"][name] = digest.hexdigest()
    lock = root.parent / (root.name + ".model-lock.json")
    lock.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Prepared {MODEL} at {REVISION}; file checksums: {lock}")


if __name__ == "__main__":
    main()
