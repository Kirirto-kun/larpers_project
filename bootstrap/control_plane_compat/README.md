# Bounded Apple Silicon worker diagnostic

This optional overlay proves a real distributed task on the already-installed
`campus/0.1.0` worker. It is a diagnostic migration bridge against pristine
control-plane commit `96bfe6d`, not a general production API agreement.

The published Assignment has an absolute task deadline, nested inputs, output
objects, aggregate disk/GPU resources, and a policy identifier. The installed
worker requires a relative hard deadline, flat artifacts/output names, explicit
PID/workspace/output/checkpoint/tmp quotas, and a policy object. Its artifact
upload also stores a basename while completion reports `workspace/<basename>`.
The bridge addresses these differences only for an administrator-allowlisted
authenticated device using the exact `campus/0.1.0` user agent.

The trusted policy fixes one native ARM image and adapter, 1 CPU, 256 MiB RAM,
64 PIDs, 34 MiB total tmpfs quotas, and a maximum 60-second hard task bound.
It permits one required workspace file, no inputs, dependencies, checkpoints,
or GPU. The scheduler rejects unsupported work before leasing it and reserves
the diagnostic adapter from every other device. A relative bound is computed
from the database server's deadline and current time at lease creation. The
worker's existing monotonic lease guard and independent hard bound stay active.

Completion removes exactly one `workspace/` prefix from a single safe basename,
then runs the ordinary fencing, artifact ownership/name/size, manifest digest,
idempotency, and verification paths. Other workers retain the published DTO.
Server verification is explicitly `structural`; numerical correctness is
checked independently after authenticated artifact download.

`fit_model.py` fits a quadratic model to 250,000 deterministic noisy samples
using floating-point Gaussian elimination. `reference.py` independently checks
the coefficients using exact rational Cramer's rule and computes MSE analytically.
This is a small CPU computation diagnostic, not a performance benchmark or a
claim of Metal/GPU support.

## Prepare and verify

1. Keep the original `96bfe6d` source snapshot unchanged and copy it to a staging
   directory. Run `python bootstrap/control_plane_compat/apply.py STAGED/server/campus`
   to validate exact pristine source hashes. Add `--apply` only after review.
2. Run `python -m unittest discover -s bootstrap/control_plane_compat/tests -v`.
   The tests cover allowlisting, quota validation, unsupported-stage rejection,
   response translation, completion names, source patching, and independent
   numerical verification, including deliberate corruption.
3. Run `server_integration.py` in a disposable API container with
   `TEST_SERVER_ROOT` pointing to the patched server and `DATABASE_URL` supplied
   privately. It creates and drops only a unique `test_<UUIDhex>` schema, asserts
   `current_schema()` before fixture creation, and runs migrations and every
   Campus operation with that search path. It verifies real HTTP/API semantics
   using a synthetic worker fixture. This is separate from physical Mac evidence.
4. Optional `EXPORT_FIXTURE` writes a sanitized rich Assignment for a Go test
   overlay exercising the installed client’s `ClaimWork` and `Assignment.Validate`.
   The exported lease token is non-authoritative. Run
   `python bootstrap/control_plane_compat/verify_legacy_client.py --fixture FIXTURE`
   (and `--go PATH` when Go is not on PATH). This uses the included Go test through
   an overlay and leaves runner source untouched.

## Deploy and run

The administrator mounts `mac-catalog.json` and `mac-policy.json` read-only and
sets `CATALOG_FILE=/catalog/mac-catalog.json` and
`CAMPUS_DIAGNOSTIC_WORKER_POLICY=/catalog/mac-policy.json` on the staged API.
The supplied policy targets the enrolled Mac used in this diagnostic; changing
devices requires an explicit administrator policy update and API restart.
No worker binaries, node credentials, or invitations need to be replaced.

`python bootstrap/control_plane_compat/run_mac.py --output OUTPUT` only writes a
reviewable plan. Adding `--submit` submits it through ordinary preview validation
and job creation, waits for the real worker, and downloads its accepted artifact.
It requires the returned task's node ID to equal the allowlisted Mac, verifies
artifact length/SHA-256, requires a native ARM Linux container report, and checks
the independent numerical reference. It saves submission, job, model, and report
JSON without credentials. A failed or incomplete job exits with an error and
keeps the available evidence. The runtime credentials and CA are read privately
from `%LOCALAPPDATA%/Campus/server-96bfe6d` by default.

Removing the overlay and the two environment settings restores the original API.
Do not advertise this restricted diagnostic as compatibility for arbitrary jobs.
