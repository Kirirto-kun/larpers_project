"""Tensor + tagged JSON state encoding. Never invokes pickle or user classes."""
from __future__ import annotations

import numpy as np
import torch
from safetensors import SafetensorError
from safetensors.torch import load, save

from workloads.common import canonical, parse_json


def encode_state(value):
    tensors = {}

    def visit(item):
        if isinstance(item, torch.Tensor):
            name = f"tensor_{len(tensors)}"
            tensors[name] = item.detach().cpu().contiguous().clone()
            return {"type": "tensor", "name": name}
        if isinstance(item, np.ndarray):
            if item.dtype.kind not in "uifb" or item.size > 100000:
                raise ValueError("unsupported RNG array")
            return {"type": "array", "dtype": str(item.dtype), "values": item.tolist()}
        if isinstance(item, dict):
            return {"type": "dict", "items": [[visit(k), visit(v)] for k, v in item.items()]}
        if isinstance(item, (tuple, list)):
            return {"type": "tuple" if isinstance(item, tuple) else "list", "items": [visit(v) for v in item]}
        if item is None or type(item) in (str, int, float, bool):
            return item
        raise ValueError(f"unsupported checkpoint state type: {type(item).__name__}")

    tree = {"schema_version": "1", "state": visit(value)}
    return {"state.json": canonical(tree), "tensors.safetensors": save(tensors)}


def decode_state(files):
    if set(files) != {"state.json", "tensors.safetensors"}:
        raise ValueError("checkpoint must contain complete safe state payloads")
    tree = parse_json(files["state.json"])
    if tree.get("schema_version") != "1":
        raise ValueError("unsupported training checkpoint state schema")
    try:
        tensors = load(files["tensors.safetensors"])
    except SafetensorError as exc:
        raise ValueError("invalid checkpoint safetensors payload") from exc

    def visit(item, depth=0):
        if depth > 50:
            raise ValueError("checkpoint state nesting exceeds limit")
        if not isinstance(item, dict):
            if item is None or type(item) in (str, int, float, bool):
                return item
            raise ValueError("invalid checkpoint state")
        kind = item.get("type")
        if kind == "tensor":
            return tensors[item["name"]]
        if kind == "array":
            dtype = np.dtype(item["dtype"])
            if dtype.kind not in "uifb":
                raise ValueError("unsafe array dtype")
            return np.asarray(item["values"], dtype=dtype)
        if kind in {"tuple", "list"}:
            values = [visit(v, depth + 1) for v in item["items"]]
            return tuple(values) if kind == "tuple" else values
        if kind == "dict":
            return {visit(k, depth + 1): visit(v, depth + 1) for k, v in item["items"]}
        raise ValueError("unknown checkpoint state tag")

    return visit(tree["state"])
