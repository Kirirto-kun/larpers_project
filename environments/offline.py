"""Trusted unprivileged Python project preparation; never imports project metadata.

Called by the guard's isolated trusted child before user argv. The guard still
owns identity, mounts, network policy, resource limits, lease and hard deadline.
"""
from __future__ import annotations

from contextlib import contextmanager
from email.parser import BytesParser
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform as platform_module
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import tomllib
import venv
import zipfile

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import parse_tag, sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

MAX_METADATA = 1 << 20
MAX_REQUIREMENTS = 256
MAX_WHEELS = 512
MAX_WHEEL_BYTES = 512 << 20
MAX_HOUSE_BYTES = 2 << 30
PREPARATION_TIMEOUT = 120
PIP_VERSION = "25.3"
SUPPORTED_PLATFORMS = {"manylinux_2_28_x86_64", "manylinux_2_28_aarch64"}
_GUIDANCE = "Choose a compatible prepared profile or prepare a binary-only wheelhouse before the offline run."

# Additional defense for pip itself, independently of the guard's network=none.
# This bootstrap executes in a fresh isolated overlay interpreter, after only
# trusted path-only .pth setup, and before importing the pinned pip implementation.
_OFFLINE_PIP_BOOTSTRAP = '''
import runpy, sys
def _deny_network(event, arguments):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.bind"}:
        raise RuntimeError("network disabled during Campus offline preparation")
sys.addaudithook(_deny_network)
sys.argv = ["pip", *sys.argv[1:]]
runpy.run_module("pip", run_name="__main__")
'''


def _fail(message):
    raise ValueError(f"{message}. {_GUIDANCE}")


def _no_links(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            _fail("symlink/junction paths are unsupported")
    return path


@contextmanager
def _open_regular(path, limit):
    path = _no_links(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        _fail(f"required regular file is unavailable: {path.name} ({exc.strerror})")
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            _fail(f"file is not regular or exceeds the size limit: {path.name}")
        yield stream


def _read_regular(path, limit=MAX_METADATA):
    with _open_regular(path, limit) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        _fail("metadata exceeds size limit")
    return raw


def _text(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        _fail("project metadata must be UTF-8")


def _uncomment(line):
    quote = None
    for i, char in enumerate(line):
        if char in {"'", '"'}:
            quote = None if quote == char else (char if quote is None else quote)
        if char == "#" and quote is None and (i == 0 or line[i - 1].isspace()):
            return line[:i].strip()
    return line.strip()


def parse_requirement(value, *, exact=False):
    if not isinstance(value, str) or not value or len(value) > 4096 or "\n" in value or "\r" in value or "\\" in value or value.startswith("-"):
        _fail("only plain PEP 508 package requirements are supported; no includes, options or editables")
    try:
        requirement = Requirement(value)
    except InvalidRequirement as exc:
        _fail(f"unsupported requirement: {exc}")
    if requirement.url is not None:
        _fail("VCS, URL, file and local-path requirements are forbidden")
    if exact:
        specs = list(requirement.specifier)
        if len(specs) != 1 or specs[0].operator != "==" or "*" in specs[0].version or requirement.marker is not None:
            _fail("wheelhouse input must use exact name==version pins without target-ambiguous markers")
        Version(specs[0].version)
    return requirement


def parse_requirements(raw, *, exact=False):
    result = []
    for line in _text(raw).splitlines():
        value = _uncomment(line)
        if value:
            result.append(parse_requirement(value, exact=exact))
        if len(result) > MAX_REQUIREMENTS:
            _fail("requirement count exceeds limit")
    return result


def _applies(requirement, extras=()):
    return requirement.marker is None or any(requirement.marker.evaluate({**default_environment(), "extra": extra}) for extra in ("", *extras))


def _python_compatible(value, context):
    try:
        if not isinstance(value, str) or not SpecifierSet(value).contains(platform_module.python_version(), prereleases=True):
            _fail(f"{context} requires-python is incompatible with Python {platform_module.python_version()}")
    except InvalidSpecifier:
        _fail(f"{context} has invalid requires-python metadata")


def read_project_requirements(root):
    root = _no_links(root).resolve(strict=True)
    if not root.is_dir():
        _fail("selected project root must be a directory")
    requirements = []
    text_file, project_file = root / "requirements.txt", root / "pyproject.toml"
    has_requirements = text_file.exists() or text_file.is_symlink()
    if has_requirements:
        requirements.extend(parse_requirements(_read_regular(text_file)))
    if project_file.exists() or project_file.is_symlink():
        try:
            metadata = tomllib.loads(_text(_read_regular(project_file)))
        except tomllib.TOMLDecodeError as exc:
            _fail(f"invalid static pyproject.toml: {exc}")
        project = metadata.get("project")
        if project is None and not has_requirements:
            _fail("pyproject has no static [project] dependencies; provide requirements.txt")
        if project is not None:
            if not isinstance(project, dict):
                _fail("project metadata must be a static table")
            dynamic = project.get("dynamic", [])
            if not isinstance(dynamic, list) or any(not isinstance(x, str) for x in dynamic) or {"dependencies", "requires-python"} & set(dynamic):
                _fail("dynamic dependencies/requires-python are unsupported; provide explicit static metadata")
            values = project.get("dependencies", [])
            if not isinstance(values, list) or len(values) > MAX_REQUIREMENTS:
                _fail("project.dependencies must be a bounded static list")
            requirements.extend(parse_requirement(value) for value in values)
            if "requires-python" in project:
                _python_compatible(project["requires-python"], "project")
    if len(requirements) > MAX_REQUIREMENTS:
        _fail("combined requirement count exceeds limit")
    return [requirement for requirement in requirements if _applies(requirement)]


def _base_sites():
    # Launcher uses the fixed trusted profile interpreter in isolated mode. Never
    # accept a site-packages path or PYTHONPATH from project metadata/user argv.
    return tuple(dict.fromkeys(str(Path(sysconfig.get_path(key)).resolve()) for key in ("purelib", "platlib")))


def _distributions(paths):
    result = {}
    for directory in paths:
        for distribution in importlib.metadata.distributions(path=[str(directory)]):
            name = distribution.metadata.get("Name")
            if name:
                result.setdefault(canonicalize_name(name), distribution)
    return result


def _missing(requirements, paths):
    installed = _distributions(paths)
    queue = list(requirements)
    checked, missing = set(), []
    while queue:
        requirement = queue.pop(0)
        key = str(requirement)
        if key in checked:
            continue
        checked.add(key)
        if len(checked) > 2048:
            _fail("installed dependency graph exceeds limit")
        dist = installed.get(canonicalize_name(requirement.name))
        if dist is None or not requirement.specifier.contains(dist.version, prereleases=True):
            missing.append(requirement)
            continue
        if dist.metadata.get("Requires-Python"):
            _python_compatible(dist.metadata["Requires-Python"], f"profile package {requirement.name}")
        provided_extras = {canonicalize_name(value) for value in dist.metadata.get_all("Provides-Extra", [])}
        if any(canonicalize_name(extra) not in provided_extras for extra in requirement.extras):
            _fail(f"profile package {requirement.name} does not provide requested extras")
        for raw in dist.requires or []:
            dependency = parse_requirement(raw)
            if _applies(dependency, requirement.extras):
                queue.append(dependency)
    return missing


def _strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                _fail("duplicate wheelhouse manifest key")
            result[key] = value
        return result
    def invalid(value):
        _fail("nonfinite wheelhouse manifest value")
    return json.loads(_text(raw), object_pairs_hook=pairs, parse_constant=invalid)


def inspect_wheel(path):
    """Hash and inspect regular wheel bytes/metadata without extracting/importing."""
    path = Path(path)
    try:
        name, version, _, tags = parse_wheel_filename(path.name)
        with _open_regular(path, MAX_WHEEL_BYTES) as stream:
            info = os.fstat(stream.fileno())
            sha, count = hashlib.sha256(), 0
            while block := stream.read(1 << 20):
                count += len(block)
                if count > MAX_WHEEL_BYTES:
                    _fail("wheelhouse wheel exceeds quota")
                sha.update(block)
            stream.seek(0)
            with zipfile.ZipFile(stream) as archive:
                entries = archive.infolist()
                if len(entries) > 100000 or sum(entry.file_size for entry in entries) > MAX_HOUSE_BYTES:
                    _fail("wheelhouse archive exceeds unpacked quota")
                names = set()
                for entry in entries:
                    relative = PurePosixPath(entry.filename)
                    if (entry.filename in names or "\\" in entry.filename or ":" in entry.filename or relative.is_absolute()
                            or ".." in relative.parts or stat.S_ISLNK(entry.external_attr >> 16)):
                        _fail("wheelhouse archive contains unsafe paths/links")
                    names.add(entry.filename)
                metadata_names = [value for value in names if value.endswith(".dist-info/METADATA")]
                if len(metadata_names) != 1:
                    _fail("wheelhouse wheel requires one static distribution metadata file")
                metadata_name = metadata_names[0]
                wheel_name = metadata_name.rsplit("/", 1)[0] + "/WHEEL"
                def read_member(member):
                    if archive.getinfo(member).file_size > MAX_METADATA:
                        _fail("wheelhouse metadata exceeds quota")
                    with archive.open(member) as member_stream:
                        raw = member_stream.read(MAX_METADATA + 1)
                    if len(raw) > MAX_METADATA:
                        _fail("wheelhouse metadata exceeds quota")
                    return BytesParser().parsebytes(raw)
                metadata, wheel = read_member(metadata_name), read_member(wheel_name)
                if canonicalize_name(metadata.get("Name", "")) != name or Version(metadata.get("Version", "")) != version:
                    _fail("wheelhouse filename and static package identity disagree")
                declared_tags = {tag for raw in wheel.get_all("Tag", []) for tag in parse_tag(raw)}
                if declared_tags != tags or wheel.get("Wheel-Version") != "1.0":
                    _fail("wheelhouse wheel tags/version metadata disagree")
                requirements = [parse_requirement(value) for value in metadata.get_all("Requires-Dist", [])]
                requires_python = metadata.get("Requires-Python", "")
                SpecifierSet(requires_python)
        record = {"filename": path.name, "name": str(name), "version": str(version), "size_bytes": info.st_size,
                  "sha256": sha.hexdigest(), "tags": sorted(str(tag) for tag in tags)}
        return record, {"tags": tags, "requires_python": requires_python, "requirements": requirements}
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        _fail(f"invalid wheelhouse wheel {path.name}: {exc}")


def verify_wheelhouse(directory):
    root = _no_links(directory)
    try:
        manifest = _strict_json(_read_regular(root / "manifest.json"))
        if (not isinstance(manifest, dict) or manifest.get("schema_version") != "1" or manifest.get("kind") != "campus-wheelhouse"
                or manifest.get("platform") not in SUPPORTED_PLATFORMS or manifest.get("python_version") != "3.11"):
            _fail("unsupported wheelhouse manifest/platform/python version")
        files = manifest.get("files")
        if not isinstance(files, list) or len(files) > MAX_WHEELS:
            _fail("wheelhouse file count exceeds quota")
        names = []
        records = []
        total = 0
        for expected in files:
            name = expected["filename"]
            if not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name:
                _fail("wheelhouse manifest contains unsafe filename")
            actual, metadata = inspect_wheel(root / name)
            if expected != actual:
                _fail(f"wheelhouse checksum/size/identity mismatch for {name}")
            total += actual["size_bytes"]
            names.append(name)
            records.append((actual, metadata))
        if len(set(names)) != len(names) or total > MAX_HOUSE_BYTES:
            _fail("wheelhouse duplicates or total size quota exceeded")
        if {path.name for path in root.iterdir()} != {"manifest.json", *names}:
            _fail("wheelhouse contains unlisted files")
        return records
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _fail(f"wheelhouse verification failed: {exc}")


def _compatible_wheels(missing, wheels):
    runtime_tags = set(sys_tags())
    for requirement in missing:
        if not any(record["name"] == canonicalize_name(requirement.name)
                   and requirement.specifier.contains(record["version"], prereleases=True)
                   and metadata["tags"] & runtime_tags
                   and SpecifierSet(metadata["requires_python"]).contains(platform_module.python_version(), prereleases=True)
                   for record, metadata in wheels):
            _fail(f"wheelhouse has no compatible binary wheel for {requirement}")


def _pip_environment():
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PIP_") and key not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1",
               PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    return env


def _run_bounded(argv, *, env, timeout, log_directory):
    # Guard disk/time/resource quotas cover this unprivileged subprocess too;
    # output is spooled rather than accumulated unbounded in coordinator memory.
    with tempfile.TemporaryFile(dir=log_directory) as log:
        try:
            result = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            _fail("offline dependency preparation exceeded its time limit")
        if result.returncode:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 16384))
            detail = log.read(16384).decode("utf-8", errors="replace")
            _fail(f"offline binary dependency preparation failed: {detail}")


def prepare_and_exec(argv, root="/workspace", wheelhouse="/opt/campus/wheelhouse"):
    """Prepare only an overlay inside selected writable workspace, then direct exec.

    This function must run as the workload UID, inside the same guard limits and
    network-none container, with this trusted module loaded by an isolated Python.
    """
    if (not isinstance(argv, (list, tuple)) or not 1 <= len(argv) <= 256
            or any(not isinstance(value, str) or not value or len(value) > 16384 or "\0" in value for value in argv)):
        _fail("user command requires bounded argv strings")
    argv = list(argv)
    root = _no_links(root).resolve(strict=True)
    requirements = read_project_requirements(root)
    sites = _base_sites()
    missing = _missing(requirements, sites)
    env = dict(os.environ)
    if missing:
        wheels = verify_wheelhouse(wheelhouse)
        _compatible_wheels(missing, wheels)
        installed = _distributions(sites)
        if installed.get("pip") is None or installed["pip"].version != PIP_VERSION:
            _fail(f"prepared profile must include pinned pip=={PIP_VERSION}")
        overlay = root / ".campus-env"
        if overlay.exists() or overlay.is_symlink():
            _fail("workspace .campus-env already exists; use a fresh extracted workspace")
        try:
            venv.EnvBuilder(with_pip=False, symlinks=False).create(overlay)
            overlay_python = overlay / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            overlay_site = overlay / ("Lib/site-packages" if os.name == "nt" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
            overlay_site.mkdir(parents=True, exist_ok=True)
            (overlay_site / "campus_base.pth").write_text("\n".join(sites) + "\n", encoding="utf-8")
            requirements_file = overlay / "campus-requirements.txt"
            requirements_file.write_text("\n".join(str(value) for value in requirements) + "\n", encoding="utf-8")
            command = [str(overlay_python), "-I", "-c", _OFFLINE_PIP_BOOTSTRAP, "install", "--no-index", "--only-binary=:all:",
                       "--find-links", str(Path(wheelhouse).resolve()), "--no-cache-dir", "--no-compile", "--no-input",
                       "--disable-pip-version-check", "--requirement", str(requirements_file)]
            _run_bounded(command, env=_pip_environment(), timeout=PREPARATION_TIMEOUT, log_directory=overlay)
            if _missing(requirements, [str(overlay_site), *sites]):
                _fail("wheelhouse installation did not satisfy the project requirements")
            env["PATH"] = str(overlay_python.parent) + os.pathsep + env.get("PATH", "")
            env["VIRTUAL_ENV"] = str(overlay)
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)
            if argv[0] in {"python", "python3", "python3.11", "python.exe", sys.executable}:
                argv[0] = str(overlay_python)
        except BaseException:
            # This is a fresh, exclusively created overlay at a checked constant
            # child path, never a caller-provided directory or preexisting tree.
            if overlay.parent == root and overlay.name == ".campus-env" and not overlay.is_symlink():
                shutil.rmtree(overlay, ignore_errors=True)
            raise
    os.chdir(root)
    if os.name == "nt":
        # Windows CRT execv concatenates arguments without correct quoting.
        # Native CreateProcess through subprocess preserves argv without a shell;
        # production Linux profiles use the direct replacement below.
        raise SystemExit(subprocess.run(argv, env=env, check=False).returncode)
    os.execvpe(argv[0], argv, env)
