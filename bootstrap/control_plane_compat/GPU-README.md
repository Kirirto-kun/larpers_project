# Bounded RTX 5050 CUDA diagnostic

The physical laptop completed this diagnostic successfully; see
[GPU_RESULT.md](GPU_RESULT.md) for the recorded device, job, artifact, and full
independent numerical comparison.

This extends the opt-in worker bridge while preserving the original version 1
Mac policy. `combined-policy.json` is a version 2 wrapper with distinct device
and adapter allowlists. The GPU entry requires its exact immutable image,
`linux/amd64`, CUDA, authenticated `campus/0.1.0` device identity, and verified
NVIDIA device ID `0`. Both diagnostic adapters remain reserved from other nodes.

The GPU assignment has a server-computed relative hard deadline of at most 60
seconds, 1000m CPU, 1 GiB RAM, 128 PIDs, and 34 MiB total tmpfs quotas. It requests
one GPU slot and 1 GiB VRAM. VRAM is a scheduling reservation: the existing worker
does not enforce a device-wide hard VRAM quota. The trusted workload sets a
cooperative 1 GiB PyTorch allocator ceiling before creating tensors and reports
peak allocated/reserved memory. CUDA context and other non-allocator memory are
outside this cooperative bound. No network, inputs, dependencies, or checkpoint
resume are admitted.

`cuda_matmul.py` performs 64 float32 256×256 CUDA matrix multiplications with
deterministic integer inputs between 0 and 7. Every exact output is below 2^24.
TF32 is disabled. Before int32 serialization, a float32→int32→float32 equality
check rejects fractional or non-finite results. The artifact contains the full
matrix and SHA-256 of explicitly little-endian int32 values.

`gpu_reference.py` independently computes all 65,536 complete integer dot products
on the CPU and compares every element plus its digest. `run_gpu.py` uses ordinary
preview/job APIs, requires the assigned task's physical node ID, downloads the
artifact with authentication and strict CA validation under an explicit 1 MiB
bound, and checks declared size/SHA-256 before numerical verification. The Mac
download default remains 64 KiB. Server acceptance remains structural; the full
numerical check is an independent diagnostic caller check. This verifies small
genuine CUDA computation, not training capacity or GPU performance.

## Review and deployment

Run `python -m unittest discover -s bootstrap/control_plane_compat/tests -v`.
`server_integration.py` keeps its Mac default; `DIAGNOSTIC_KIND=gpu` selects the
GPU combined-catalog test, including rejection of a verified GPU with ID `1`.
Both runs create/drop only independently generated `test_<UUIDhex>` schemas and
assert the selected schema before creating fixture pool data. Synthetic fixture
uploads are integration evidence, separate from a physical diagnostic run.

Generate the overlay from pristine commit `96bfe6d` using `apply.py`. Relative to
the Mac overlay, `main.py` stays byte-for-byte unchanged; scheduler admission now
passes `node=node` so the helper can check the exact GPU ID. Mount this directory
read-only as `/catalog` and configure the API with:

```
CATALOG_FILE=/catalog/combined-catalog.json
CAMPUS_DIAGNOSTIC_WORKER_POLICY=/catalog/combined-policy.json
```

`python bootstrap/control_plane_compat/run_gpu.py --output .git/campus-runner/rtx5050-evidence`
only prepares a reviewable plan. Add `--submit` only after review and deployment
to perform the physical GPU job. Credentials stay in the private local runtime;
the output directory contains no credentials. Preserve the submitted plan,
submission/job metadata, original artifact, and compact verification report.
