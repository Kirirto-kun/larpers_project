"""Deterministic, resumable registered image-classifier recipe."""
from .runtime import configure_cublas

# Package entry precedes adapter/state/standalone CLI imports and any Torch
# import performed by this training runtime.
configure_cublas()
