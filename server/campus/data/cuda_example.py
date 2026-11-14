"""Small exact CUDA computation; executed inside the pinned native runtime."""

import hashlib
import json
import platform
import struct
import time

import torch

torch.set_num_threads(1)
if not torch.cuda.is_available():
    raise RuntimeError("CUDA is unavailable")
torch.cuda.set_device(0)
properties = torch.cuda.get_device_properties(0)
limit = 1 << 30
torch.cuda.set_per_process_memory_fraction(limit / properties.total_memory, 0)
torch.backends.cuda.matmul.allow_tf32 = False
torch.set_float32_matmul_precision("highest")
n = 256
rows = torch.arange(n, device="cuda", dtype=torch.int64).view(n, 1)
cols = torch.arange(n, device="cuda", dtype=torch.int64).view(1, n)
a = ((rows * 3 + cols * 5 + 1) % 8).to(torch.float32)
b = ((rows * 7 + cols * 2 + 3) % 8).to(torch.float32)
torch.cuda.synchronize()
started = time.perf_counter()
for _ in range(64):
    result = torch.mm(a, b)
torch.cuda.synchronize()
elapsed = time.perf_counter() - started
integer = result.to(dtype=torch.int32)
if not torch.equal(result, integer.to(dtype=torch.float32)):
    raise RuntimeError("CUDA result contains non-integral or non-finite values")
matrix = integer.to(device="cpu").tolist()
flat = [item for row in matrix for item in row]
report = {
    "system": platform.system(),
    "machine": platform.machine(),
    "cuda_available": torch.cuda.is_available(),
    "device_name": properties.name,
    "device_index": torch.cuda.current_device(),
    "compute_capability": list(torch.cuda.get_device_capability(0)),
    "torch_version": torch.__version__,
    "cuda_version": torch.version.cuda,
    "dimension": n,
    "repetitions": 64,
    "elapsed_seconds": elapsed,
    "matrix": matrix,
    "matrix_sha256": hashlib.sha256(struct.pack("<65536i", *flat)).hexdigest(),
    "max_memory_allocated": torch.cuda.max_memory_allocated(0),
    "max_memory_reserved": torch.cuda.max_memory_reserved(0),
    "allocator_limit_bytes": limit,
}
with open("model.json", "w") as output:
    json.dump(report, output, separators=(",", ":"))
print(json.dumps({k: v for k, v in report.items() if k != "matrix"}))
