# Physical RTX 5050 computation result

The enrolled Windows 11 AMD laptop completed a real CUDA job through the live
coordinator on 2026-11-15 (Asia/Shanghai). The existing worker binary received
the task, ran its pinned Linux/amd64 CUDA container, and uploaded the result.
The reviewed combined compatibility policy preserves the earlier Mac diagnostic.

| Evidence | Value |
| --- | --- |
| Physical worker | `ec6c1cbe-125d-43e4-bda6-a82b0fcdc34a` |
| GPU | NVIDIA GeForce RTX 5050 Laptop GPU, compute capability 12.0 |
| Reported VRAM / driver | 8,546,942,976 bytes / 610.88 |
| Runtime | PyTorch 2.9.1+cu128, CUDA 12.8, Linux x86_64 |
| Job | `d2bc48d7-6c4b-47c4-901c-b0373009eb31` |
| Task | `db347f14-310e-4807-a22f-26da8c000f67` |
| Attempt | `e933ab58-4bb3-4550-8603-949bd4fdcae9` |
| Final job / task | succeeded / succeeded, generation 1 |
| Artifact | `6afc25c1-748b-4947-87e8-71ed74e5fe34`, model.json, 328,661 bytes |
| Artifact SHA-256 | `73e66489e8e682582fb487d6cfb33153d1e5de0e76ec6451538a21735ca29f08` |
| Matrix SHA-256 | `c80c02c52733ea80a50a317e411d955f8971866f8be4321590cd893485a1cc94` |

The workload performed 64 deterministic 256 by 256 CUDA matrix multiplications.
The reported synchronized calculation time was 264.530 ms; peak PyTorch allocated
memory was 34,935,296 bytes and reserved memory was 35,651,584 bytes. This small
functional diagnostic is not a GPU benchmark or a training-capacity measurement.

The artifact was downloaded with authentication and strict CA validation. Its
declared byte length and SHA-256 matched. An independent CPU implementation
computed all 65,536 exact integer dot products: every element matched, with
maximum absolute error zero. The server's own verification level remains
`structural`; the independent numerical verification is additional caller-side
evidence.

After completion, the device returned to `ready`, with one accepted unit and
zero active attempts. Its observed heartbeat was `2026-11-14T17:05:12.392506Z`
(2026-11-15 01:05:12 in Asia/Shanghai).

The policy allows this exact device, image, adapter, and verified GPU ID 0. Its
bounds are 1 CPU, 1 GiB RAM, 128 PIDs, 34 MiB aggregate tmpfs quotas, no network,
and a maximum 60-second independent task deadline. The 1 GiB VRAM reservation
and cooperative PyTorch allocator ceiling are not device-wide hard VRAM isolation.
This result establishes the restricted diagnostic path, not general compatibility
for arbitrary published assignments.

Validation before deployment included 13 passing Python tests, isolated real HTTP
API integration for both GPU and Mac policies in separate temporary PostgreSQL
schemas, and the existing Go client's ClaimWork/Assignment.Validate against the
GPU response fixture. The patched staging source hashes were frozen before the
live API build. Neither the worker binary nor the public starter files needed
replacement for this test.

Detailed local evidence is stored under `.git/campus-runner/rtx5050-evidence/`:
reviewed source hashes, integration logs, submitted plan, submission/job metadata,
original artifact, and independent verification report. It contains no worker
credentials, owner tokens, or invitations. Runtime credentials remain private.
