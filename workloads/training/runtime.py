"""Configure deterministic cuBLAS before importing/initializing Torch or CUDA."""
import os
import sys

_APPROVED_CUBLAS = {":4096:8", ":16:8"}


def configure_cublas():
    value = os.environ.get("CUBLAS_WORKSPACE_CONFIG")
    if value is None:
        torch = sys.modules.get("torch")
        cuda = getattr(torch, "cuda", None)
        if cuda is not None and cuda.is_initialized():
            raise ValueError("CUBLAS_WORKSPACE_CONFIG must be configured before CUDA initialization; restart the training process")
        value = ":4096:8"
        os.environ["CUBLAS_WORKSPACE_CONFIG"] = value
    if value not in _APPROVED_CUBLAS:
        raise ValueError("unsupported CUBLAS_WORKSPACE_CONFIG; approved values are :4096:8 or :16:8, configured before CUDA initialization")
    return value
