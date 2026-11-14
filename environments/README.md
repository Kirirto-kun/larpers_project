# Prepared Python profiles and offline project dependencies

The runner selects an approved pinned CPU/CUDA image. The guard launches the
trusted `environments.offline.prepare_and_exec` module in an isolated interpreter
before starting the submitted argv. Project inspection, optional dependency
installation and user code all run as the same unprivileged workload UID, within
the same network-none, CPU/RAM/PID/disk, lease and hard-runtime limits. Dependency
preparation never installs into the root filesystem or host environment.

## Supported ordinary projects

No Dockerfile is needed for a Python project whose requirements are already in
the selected profile or its approved offline wheelhouse. The launcher reads only:

- selected-root `requirements.txt`, containing plain PEP 508 package requirements;
- selected-root `pyproject.toml`, with static `[project].dependencies` and optional
  `requires-python`.

When both files exist, their requirements are combined. Active environment
markers are evaluated, installed dependency/extras metadata is checked, and
versions must satisfy all constraints. Python version incompatibility requires a
different prepared profile. Static package metadata is read without importing
`setup.py`, executing a build backend, or importing the project. An unrelated
pyproject without `[project]` is acceptable only when explicit requirements.txt
supplies the dependencies. Dynamic dependencies or dynamic requires-python are
rejected because the launcher cannot safely infer them.

The metadata files must be bounded UTF-8 regular files without symlink/junction
paths. Reads use no-follow/nonblocking descriptors where the platform supports
them and validate regular type/size on the open descriptor. Each file is at most
1 MiB, at most 256 project requirements combined, at most 4096 characters per
requirement. This is a deliberately bounded static metadata workflow.

Requirements cannot contain VCS/HTTP/file URLs, local source paths, editables,
`-r`/`-c` includes, index options, pip arguments or source-build instructions.
Inline comments and ordinary PEP 508 version constraints/extras/markers are
supported. For unsupported project/system dependencies, the error directs the
owner to an approved prepared profile or an administrator-prepared wheelhouse;
there is no network/build fallback during the run.

## Runtime behavior

The trusted integration API is:

```python
from environments.offline import prepare_and_exec
prepare_and_exec(argv, root="/workspace", wheelhouse="/opt/campus/wheelhouse")
```

It is intended to be called from the guard's fixed trusted Python launch in `-I`
mode, with only the fixed trusted `/opt/campus` source root inserted into sys.path.
Do not load this module from user project paths or run it in the privileged
supervisor. The surrounding guard still owns the sandbox and process lifetime.

If the profile satisfies all requirements, the original argv and interpreter are
executed unchanged. In particular, registered adapter commands using
`/opt/campus/.venv/bin/python` stay on that interpreter. A missing wheelhouse is
irrelevant when no extra package is needed.

If a requirement is missing or incompatible, the launcher:

1. Verifies the prepared wheelhouse manifest and every wheel's size, SHA-256,
   filename-derived name/version/tags, safe archive paths, static METADATA/WHEEL
   identity and dependency requirements. Hidden transitive URL dependencies are
   rejected before pip starts. Source archives and unlisted files are rejected.
2. Checks that needed roots have wheels compatible with the actual interpreter's
   tags and Python version. The offline resolver also checks the full dependency
   closure; an incomplete/incompatible house fails before user code executes.
3. Creates fresh `/workspace/.campus-env` using stdlib venv without ensurepip or
   downloads. A fixed path-only `.pth` adds the trusted base interpreter's purelib
   and platlib so existing ML dependencies remain available. No project-supplied
   `.pth` or metadata code is imported by preparation.
4. Uses the profile's pinned `pip==25.3` through the overlay interpreter in isolated
   mode. Pip runs binary-only with `--no-index`, the verified local `--find-links`
   directory, no cache/version check/input, and sanitized pip configuration. An
   audit hook rejects socket connect/DNS/bind before pip imports, independently
   of the container's network-none policy. No source builds are allowed.
5. Rechecks the resulting overlay/base dependency metadata, prepends the overlay
   bin directory to PATH, and starts the original argument sequence. Conventional
   `python`, `python3`, `python3.11`, `python.exe`, and the launcher's base Python
   executable select overlay Python when preparation was needed. Other commands
   resolve through overlay-first PATH. No shell is involved.

Production Linux uses `os.execvpe` so user code replaces the launcher. Windows
local tests use native CreateProcess through subprocess because Windows CRT
execv does not preserve quoted argument boundaries; it also uses no shell.

Only the explicit workspace overlay is written. Existing `.campus-env` directories
are never trusted, reused, executed or deleted: use a fresh extracted workspace.
A newly created failed overlay is removed. The guard excludes it from captured
project outputs. Pip has a 120-second preparation timeout, bounded metadata/file
counts and sizes, spooled diagnostics with bounded error excerpts, and the guard's
independent resource/deadline limits. Its time belongs to preparation/attempt
overhead rather than free model computation.

The `.pth` adds base site-packages paths rather than executing all base `.pth`
hooks. An editable SDK installed only through such hooks needs its trusted fixed
source path exposed by profile integration or a noneditable profile installation.
This preserves the boundary against arbitrary project metadata hooks.

## Explicit online wheelhouse preparation

Preparation is an owner/admin action before the offline run. It downloads wheels,
not installs, into a new destination directory; the completed directory is copied
into the approved image and protected by its pinned image digest.

From repository root with the prepared tooling environment:

```powershell
uv run --project workloads --extra cpu python -m environments.prepare_wheelhouse --requirements ./offline-requirements.txt --to ./prepared-wheels --platform manylinux_2_28_x86_64 --python-version 311
```

Supported platform targets are `manylinux_2_28_x86_64` and
`manylinux_2_28_aarch64`, currently Python 3.11/CPython cp311. Input requirements
must use exact normal `name==version` pins. Prep rejects version ranges/wildcards
and target-ambiguous root markers; runtime project constraints can use ranges.
Use the matching target Linux preparation environment for packages with
platform-conditional dependency metadata; cross-target pip wheel selection alone
does not establish a complete target marker environment. The runtime resolver
still refuses missing closure rather than contacting a network.

The command uses pinned pip's `download --only-binary=:all:` against the explicit
PyPI index with a 300-second timeout and cleared inherited pip configuration.
It never invokes user build backends or installs a source distribution. Downloads
are staged, every wheel is inspected, and a manifest is atomically published only
when preparation succeeds. Existing destinations are not overwritten and files
are made read-only to discourage accidental changes. The image mount's read-only
protection, not host file modes, is the runtime trust boundary.

The exact manifest is `manifest.json`:

```json
{
  "schema_version": "1",
  "kind": "campus-wheelhouse",
  "platform": "manylinux_2_28_x86_64",
  "python_version": "3.11",
  "requirements": ["example-package==1.0.0"],
  "files": [{
    "filename": "example_package-1.0.0-py3-none-any.whl",
    "name": "example-package",
    "version": "1.0.0",
    "size_bytes": 1234,
    "sha256": "REPLACE_WITH_ACTUAL_64_HEX_SHA256",
    "tags": ["py3-none-any"]
  }]
}
```

The preparer writes actual values. Names/versions/tags are normalized from the
wheel filename and checked against its static metadata. Runtime allows up to 512
wheels, 512 MiB per wheel, 2 GiB total and bounded archive metadata/unpacked sizes.
Only listed wheels and manifest.json can exist at the top level. An empty verified
house is valid for profiles needing no extra dependency. A checksum lock records
bytes; administrator approval and the immutable trusted image establish which
bytes are authorized. Runtime never downloads from a manifest URL.

## Verification

```powershell
uv run --offline --project workloads --extra cpu python -m pytest environments/tests -q
```

Tests install actual hand-built local wheels with pinned pip, retain existing
profile packages, override an incompatible base version only in the overlay,
resolve a transitive local wheel, preserve Unicode/quotes/spaces in argv, and
exercise static metadata rejection, wheel checksums/tags, missing wheels,
symlinks, file quotas and subprocess timeouts. An actual pip network attempt is
blocked by the bootstrap's audit hook. Tests do not establish Docker image builds,
guard UID/mount/deadline behavior or every real package/platform's compatibility;
those are separate profile/runtime acceptance checks.
