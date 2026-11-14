"""Independent exact integer CPU reference for every CUDA output element."""
import hashlib
import struct


def verify(model):
    if (model.get("system") != "Linux" or model.get("machine") not in {"x86_64", "amd64"}
            or model.get("cuda_available") is not True or model.get("device_index") != 0
            or "RTX 5050" not in model.get("device_name", "")
            or model.get("compute_capability") != [12, 0]
            or model.get("dimension") != 256 or model.get("repetitions") != 64
            or model.get("allocator_limit_bytes") != 1 << 30
            or not str(model.get("torch_version", "")).startswith("2.9.1")
            or model.get("cuda_version") != "12.8"):
        raise ValueError("native RTX 5050 CUDA runtime identity differs")
    for field in ("max_memory_allocated", "max_memory_reserved"):
        if type(model.get(field)) is not int or not 0 < model[field] <= 1 << 30:
            raise ValueError("reported PyTorch allocator exceeded diagnostic bound")
    matrix = model.get("matrix")
    if not isinstance(matrix, list) or len(matrix) != 256:
        raise ValueError("missing full matrix")
    values = []
    for i, row in enumerate(matrix):
        if not isinstance(row, list) or len(row) != 256:
            raise ValueError("incorrect matrix shape")
        for j, actual in enumerate(row):
            # Intentionally computes the complete dot product on the CPU.
            expected = sum(((3*i + 5*k + 1) % 8) * ((7*k + 2*j + 3) % 8)
                           for k in range(256))
            if type(actual) is not int or actual != expected:
                raise ValueError(f"CUDA matrix differs at row {i}, column {j}")
            values.append(actual)
    digest = hashlib.sha256(struct.pack("<65536i", *values)).hexdigest()
    if model.get("matrix_sha256") != digest:
        raise ValueError("matrix digest differs")
    return {"accepted": True, "compared_elements": 65536,
            "reference": "independent full exact integer CPU dot products",
            "matrix_sha256": digest, "max_absolute_error": 0}
