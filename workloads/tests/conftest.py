import hashlib
import json
from pathlib import Path

import pytest


def digest_files(directory):
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file()}


@pytest.fixture(scope="session")
def tiny_model_dir(tmp_path_factory):
    """Real, random local BERT fixture; never represented as pretrained E5."""
    import torch
    from transformers import BertConfig, BertModel, BertTokenizerFast
    root = tmp_path_factory.mktemp("offline-bert")
    vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "query", "passage", ":",
             "hello", "world", "campus", "pool", "red", "green", "blue", "document"]
    (root / "vocab.txt").write_text("\n".join(vocab), encoding="utf-8")
    BertTokenizerFast(vocab_file=str(root / "vocab.txt"), do_lower_case=True,
                      model_max_length=512).save_pretrained(root)
    torch.manual_seed(15)
    model = BertModel(BertConfig(vocab_size=len(vocab), hidden_size=384,
                                num_hidden_layers=1, num_attention_heads=6,
                                intermediate_size=64, max_position_embeddings=512))
    model.save_pretrained(root, safe_serialization=True)
    return root


@pytest.fixture
def embedding_manifest(tmp_path, tiny_model_dir):
    path = tmp_path / "multilingual 文档.jsonl"
    path.write_text('\n'.join(json.dumps({"id": str(i), "text": value}, ensure_ascii=False)
                              for i, value in enumerate(["hello world", "campus pool", "你好 世界", "red green", "blue document"])),
                    encoding="utf-8")
    return {"schema_version": "1", "kind": "embeddings",
            "model": {"id": "intfloat/multilingual-e5-small", "revision": "a" * 40,
                      "path": str(tiny_model_dir), "files": digest_files(tiny_model_dir)},
            "inputs": [{"path": str(path), "format": "jsonl", "source": "文档"}],
            "preprocessing": {"chunk_chars": 1000, "overlap_chars": 0}}


@pytest.fixture
def training_manifest(tmp_path):
    import numpy as np
    from PIL import Image
    root = tmp_path / "labeled images"
    root.mkdir()
    for split, count in [("train", 8), ("validation", 4)]:
        rows = []
        for i in range(count):
            label = "red" if i % 2 == 0 else "blue"
            pixels = np.zeros((16, 16, 3), dtype=np.uint8)
            pixels[:, :, 0 if label == "red" else 2] = 230
            pixels[0, 0] = [i + (30 if split == "validation" else 0), 17, 93]
            filename = f"{split}-{i}.png"
            Image.fromarray(pixels).save(root / filename)
            rows.append({"path": filename, "label": label})
        (root / f"{split}.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    return {"schema_version": "1", "kind": "training", "environment_digest": "e" * 64,
            "dataset": {"root": str(root), "train": "train.jsonl", "validation": "validation.jsonl",
                        "classes": ["red", "blue"]},
            "trainer": {"seed": 23, "epochs": 3, "batch_size": 2, "learning_rate": 0.02,
                        "image_size": 16, "checkpoint_every": 2}}
