"""The production encoder only loads checksum-locked local safetensors models."""
from __future__ import annotations

import os


def mean_pool(last_hidden_state, attention_mask):
    mask = attention_mask.unsqueeze(-1).to(last_hidden_state.dtype)
    return (last_hidden_state * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)


class E5Encoder:
    def __init__(self, model, device="cpu"):
        # Explicit local_files_only below is the primary boundary. These also
        # prevent optional HF integrations from trying telemetry or cache refresh.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        import torch
        from transformers import AutoModel, AutoTokenizer
        if device not in {"cpu", "cuda"}:
            raise ValueError("device must be cpu or cuda")
        if device == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA requested but no usable CUDA device is available")
        self.torch = torch
        self.device = device
        torch.set_num_threads(min(4, max(1, os.cpu_count() or 1)))
        self.tokenizer = AutoTokenizer.from_pretrained(model["path"], revision=model["revision"],
                                                     local_files_only=True, trust_remote_code=False)
        self.model = AutoModel.from_pretrained(model["path"], revision=model["revision"],
                                              local_files_only=True, trust_remote_code=False,
                                              use_safetensors=True, dtype=torch.float32)
        self.model.to(device)
        self.model.eval()

    def encode(self, prefixed_texts):
        torch = self.torch
        inputs = self.tokenizer(prefixed_texts, padding=True, truncation=True,
                                max_length=512, return_tensors="pt")
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with torch.inference_mode():
            hidden = self.model(**inputs).last_hidden_state
            embeddings = mean_pool(hidden, inputs["attention_mask"])
            embeddings = torch.nn.functional.normalize(embeddings.float(), p=2, dim=1)
        return embeddings.cpu().numpy()

    def release_oom(self):
        if self.device == "cuda":
            self.torch.cuda.empty_cache()
