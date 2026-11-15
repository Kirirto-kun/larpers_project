# Physical Mac computation result

The existing Apple Silicon Mac worker completed a real distributed job on
2026-11-14 through the live coordinator. No new Mac binary or user command was
needed. The bounded compatibility overlay described in README.md was deployed
to the API, with its explicit device/image/adapter allowlist and resource policy.

The task fitted a quadratic least-squares model to 250,000 deterministic noisy
samples inside its native Linux/aarch64 container using Python 3.11.14. The
reported calculation time was 447.717 ms. This is a functional computation
diagnostic, not a benchmark.

| Evidence | Value |
| --- | --- |
| Physical worker node | `50b752cf-f13f-4f76-ae0c-0b23f7666d35` |
| Job | `f62d56ff-b5d0-4866-aa5c-d1e0a24f00a4` |
| Task | `969fbd58-1dea-43a9-a3c8-cbff2d9b0c9d` |
| Attempt | `0375e4f3-5b9f-40c2-a5fa-7fd6f9c78229` |
| Result artifact | `b2a74c21-ae05-440e-8b2f-c820f0e941d3` |
| Artifact | `model.json`, 281 bytes |
| SHA-256 | `eb57995ae2440f334c6d8c1c8e9ef4ed41b5ed3ef29e759fa85ee43db93482f7` |
| Task generation | 1 |
| Final job/task state | succeeded / succeeded |

The artifact was downloaded through the authenticated artifact API and checked
against its declared size and SHA-256. An independent local implementation using
exact rational Cramer's rule and analytic residual error accepted the numerical
result. Maximum coefficient error was `4.0589753780295723e-13`; MSE error was
`4.6629367034256575e-15`. The server's own verification level remains explicitly
`structural`.

The server event sequence was `job_created`, `task_leased`, `attempt_started`,
`verification_pending`, `result_accepted`. The post-job worker state was `ready`,
with zero active attempts and one accepted unit. Its fresh heartbeat was
`2026-11-14T12:04:08.573437Z`.

The enforced assignment policy was 1 CPU, 256 MiB RAM, 64 PIDs, 16 MiB workspace,
1 MiB output, 1 MiB checkpoint, 16 MiB tmp, no network, no GPU, and a 60-second
independent hard task bound. This confirms the restricted diagnostic path; it
does not establish general compatibility for arbitrary published assignments.

Validation also included seven passing Python tests, a disposable-container API
integration test using a separately created/dropped PostgreSQL test schema,
and the existing Go client's `ClaimWork` plus `Assignment.Validate` against the
sanitized rich response exported by that integration test. A different enrolled
ready device with the same image could not claim the diagnostic task. Ordinary
clients retained the published Assignment DTO, and a stale attempt fence was
rejected.

Detailed local evidence is stored under
`.git/campus-runner/mac-compute-evidence/physical-mac/`: the reviewed plan,
submission, final job, actual model bytes, independent verification report,
server events, and worker state. These files contain no credentials or invitation
tokens. The original control-plane snapshot remains unchanged; the deployed
overlay was built from the separate compatibility staging copy.
