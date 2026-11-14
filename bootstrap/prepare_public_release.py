"""Prepare a secret-free, pinned public release from approved local assets.

Uses Python stdlib only. It never invokes Docker or exports/recompresses images.
Image sources must remain immutable: verified gzip archives are hardlinked into
an atomic new release directory, saving several gigabytes of duplicate storage.
"""
from __future__ import annotations
import argparse
import base64
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import struct
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit

_spec = importlib.util.spec_from_file_location('_campus_connection_builder', Path(__file__).with_name('prepare_connection_kit.py'))
kit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kit)
CHUNK = 65536
TARGETS = [(goos, arch) for goos in ('darwin', 'windows', 'linux') for arch in ('amd64', 'arm64')]


def _url(value, *, directory=False):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or not re.fullmatch(r'https://[A-Za-z0-9.:[\]-]+(?:/[A-Za-z0-9._/-]*)?', value)
            or any(p in {'.', '..', ''} for p in parsed.path.strip('/').split('/') if parsed.path.strip('/'))):
        raise ValueError('public URLs must be credential-free HTTPS with safe ASCII paths')
    try: parsed.port
    except ValueError: raise ValueError('invalid HTTPS port') from None
    if directory and (not parsed.path or parsed.path == '/'):
        raise ValueError('base URL must name a release directory')
    return value.rstrip('/')


def _binary(source, goos, arch):
    header = source.read(64)
    if goos == 'darwin':
        if (len(header) < 32 or struct.unpack('<I', header[:4])[0] != 0xFEEDFACF
                or struct.unpack('<I', header[4:8])[0] != (0x0100000C if arch == 'arm64' else 0x01000007)
                or struct.unpack('<I', header[12:16])[0] != 2):
            raise ValueError('runner must be a thin matching native Mach-O executable')
    elif goos == 'windows':
        if len(header) != 64 or header[:2] != b'MZ': raise ValueError('runner must be a native PE executable')
        offset = struct.unpack('<I', header[60:64])[0]
        if offset > 1 << 20: raise ValueError('invalid PE header offset')
        source.seek(offset); pe = source.read(26)
        if (len(pe) != 26 or pe[:4] != b'PE\0\0' or struct.unpack('<H', pe[4:6])[0] != (0xAA64 if arch == 'arm64' else 0x8664)
                or not struct.unpack('<H', pe[22:24])[0] & 2 or struct.unpack('<H', pe[24:26])[0] != 0x20B):
            raise ValueError('runner must be a matching executable PE32+ binary')
    else:
        if (len(header) != 64 or header[:6] != b'\x7fELF\x02\x01' or struct.unpack('<H', header[16:18])[0] not in {2, 3}
                or struct.unpack('<H', header[18:20])[0] != (183 if arch == 'arm64' else 62)):
            raise ValueError('runner must be a matching 64-bit little-endian ELF executable')
    source.seek(0); tail = b''; consumed = 0; found = None
    while chunk := source.read(CHUNK):
        block = tail + chunk; start = 0
        while True:
            pos = block.find(b'\xff Go buildinf:', start)
            if pos < 0: break
            absolute = consumed - len(tail) + pos
            if absolute % 16 == 0: found = absolute; break
            start = pos + 1
        if found is not None: break
        consumed += len(chunk); tail = block[-15:]
    if found is None: raise ValueError('runner has no native Go build metadata')
    source.seek(found); info = source.read(32)
    if len(info) != 32 or info[14:16] != bytes([8, 2]): raise ValueError('runner needs inline 64-bit Go build metadata')
    version, module = kit._go_string(source), kit._go_string(source)
    if not re.fullmatch(rb'go1\.[0-9]+(?:\.[0-9]+)?(?:[a-zA-Z0-9.-]*)', version) or len(module) < 32:
        raise ValueError('runner has invalid Go build metadata')
    try:
        settings = dict(line[6:].split('=', 1) for line in module[16:-16].decode('utf-8').splitlines() if line.startswith('build\t') and '=' in line[6:])
    except UnicodeError: raise ValueError('invalid Go build settings') from None
    if settings.get('GOOS') != goos or settings.get('GOARCH') != arch or settings.get('CGO_ENABLED') != '0':
        raise ValueError('runner Go target/CGO metadata does not match')
    source.seek(0)


class _HashReader:
    def __init__(self, source): self.source, self.hash, self.total = source, hashlib.sha256(), 0
    def read(self, size=-1):
        if size < 0 or size > CHUNK: raise ValueError('unbounded compressed archive read')
        value = self.source.read(size); self.hash.update(value); self.total += len(value)
        if self.total > kit.MAX_IMAGE: raise ValueError('compressed image exceeds bound')
        return value


class _RawBound:
    def __init__(self, source): self.source, self.total = source, 0
    def read(self, size=-1):
        if size < 0 or size > CHUNK: raise ValueError('unbounded image archive read')
        value = self.source.read(size); self.total += len(value)
        if self.total > kit.MAX_RAW: raise ValueError('uncompressed image exceeds bound')
        return value


def _archive_ref(source, profile):
    reader = _HashReader(source); metadata = {}; blobs = set(); names = set(); files = {}; metadata_bytes = 0
    try:
        with gzip.GzipFile(fileobj=reader, mode='rb') as compressed:
            raw = _RawBound(compressed)
            with tarfile.open(fileobj=raw, mode='r|') as archive:
                for number, entry in enumerate(archive):
                    parts = PurePosixPath(entry.name).parts
                    if (number >= 100000 or not parts or entry.name.startswith('/') or '..' in parts or '\\' in entry.name
                            or not (entry.isfile() or entry.isdir()) or entry.name in names or entry.size > kit.MAX_RAW):
                        raise ValueError('unsafe Docker image archive')
                    names.add(entry.name)
                    if not entry.isfile(): continue
                    value = archive.extractfile(entry); hashed = hashlib.sha256(); prefix = b''; small = bytearray() if entry.size <= 2 << 20 else None
                    while chunk := value.read(CHUNK):
                        hashed.update(chunk)
                        if not prefix: prefix = chunk[:4]
                        if small is not None: small.extend(chunk)
                    actual = hashed.hexdigest()
                    files[entry.name] = (actual, prefix, entry.size)
                    is_blob = bool(re.fullmatch(r'blobs/sha256/[a-f0-9]{64}', entry.name))
                    is_legacy = bool(re.fullmatch(r'[a-f0-9]{64}\.json', entry.name))
                    if (is_blob and actual != entry.name.rsplit('/', 1)[-1]) or (is_legacy and actual != entry.name[:-5]):
                        raise ValueError('Docker image blob digest mismatch')
                    if is_blob or is_legacy: blobs.add('sha256:' + actual)
                    if small is not None and (entry.name in {'index.json', 'manifest.json'} or is_blob or is_legacy):
                        try:
                            parsed = json.loads(small)
                            metadata_bytes += len(small)
                            if metadata_bytes > 8 << 20 or len(metadata) >= 4096:
                                raise RuntimeError('Docker image archive metadata exceeds memory bound')
                            metadata[entry.name if entry.name in {'index.json', 'manifest.json'} else 'sha256:' + actual] = parsed
                        except (ValueError, UnicodeError, RecursionError):
                            if entry.name in {'index.json', 'manifest.json'}: raise ValueError('invalid Docker image archive metadata') from None
            while raw.read(CHUNK): pass
        while reader.read(CHUNK): pass
    except RuntimeError as error:
        raise ValueError(str(error)) from None
    except (tarfile.TarError, EOFError, OSError, RecursionError) as error:
        raise ValueError('invalid gzip Docker image archive') from error
    identity = 'sha256:' + profile['image_digest'].rsplit('sha256:', 1)[-1]
    index = metadata.get('index.json'); configs = []
    if isinstance(index, dict):
        roots = index.get('manifests')
        if not isinstance(roots, list) or not any(isinstance(ref, dict) and ref.get('digest') == identity for ref in roots) or identity not in blobs:
            raise ValueError('archive OCI identity does not match approved profile')
        pending = [identity]; visited = set()
        while pending:
            key = pending.pop()
            if key in visited: continue
            visited.add(key); obj = metadata.get(key)
            if not isinstance(obj, dict): continue
            descriptors = ([obj['config']] if isinstance(obj.get('config'), dict) and isinstance(obj['config'].get('digest'), str) else [])
            for field in ('manifests', 'layers'):
                if isinstance(obj.get(field), list): descriptors.extend(obj[field])
            if any(not isinstance(ref, dict) or ref.get('digest') not in blobs for ref in descriptors):
                raise ValueError('archive is missing a referenced OCI blob')
            if isinstance(obj.get('config'), dict) and isinstance(obj['config'].get('digest'), str): pending.append(obj['config']['digest'])
            if isinstance(obj.get('manifests'), list): pending.extend(r['digest'] for r in obj['manifests'] if isinstance(r, dict) and isinstance(r.get('digest'), str))
            if 'architecture' in obj and 'os' in obj: configs.append(obj)
    else:
        manifest = metadata.get('manifest.json')
        if not isinstance(manifest, list) or not any(isinstance(ref, dict) and ref.get('Config') == identity[7:] + '.json' for ref in manifest) or identity not in blobs:
            raise ValueError('archive Docker config identity does not match approved profile')
        for record in manifest:
            if not isinstance(record, dict) or not isinstance(record.get('Config'), str) or not re.fullmatch(r'[a-f0-9]{64}\.json', record['Config']):
                raise ValueError('invalid legacy Docker image config reference')
            config = metadata.get('sha256:' + record['Config'][:-5])
            rootfs = config.get('rootfs') if isinstance(config, dict) else None
            layers = record.get('Layers')
            diff_ids = rootfs.get('diff_ids') if isinstance(rootfs, dict) else None
            if (not isinstance(rootfs, dict) or rootfs.get('type') != 'layers' or not isinstance(layers, list)
                    or not isinstance(diff_ids, list) or len(layers) != len(diff_ids)):
                raise ValueError('legacy Docker Layers must match ordered config rootfs.diff_ids')
            for layer, expected in zip(layers, diff_ids):
                if (not isinstance(layer, str) or not layer.endswith('.tar') or layer.startswith('/') or '\\' in layer
                        or '..' in PurePosixPath(layer).parts or layer not in files):
                    raise ValueError('legacy Docker archive references a missing or unsafe regular layer')
                actual, prefix, size = files[layer]
                if prefix.startswith(b'\x1f\x8b') or prefix == b'\x28\xb5\x2f\xfd' or size < 1024 or size % 512:
                    raise ValueError('legacy Docker layers must be uncompressed raw tar files')
                if not isinstance(expected, str) or not kit.LOCAL_ID.fullmatch(expected) or expected != 'sha256:' + actual:
                    raise ValueError('legacy Docker raw layer digest does not match ordered rootfs.diff_ids')
        configs = [metadata.get(identity)]
    matching = [c for c in configs if isinstance(c, dict) and c.get('os') == 'linux' and 'linux/' + str(c.get('architecture')) == profile['platform']]
    if not any(isinstance(c.get('config'), dict) and isinstance(c['config'].get('Labels'), dict)
               and c['config']['Labels'].get('io.campus.guard') == '1'
               and c['config']['Labels'].get('io.campus.accelerator') == profile['accelerator'] for c in matching):
        raise ValueError('archive native platform and guard labels do not match approved profile')
    return {'sha256': reader.hash.hexdigest(), 'size_bytes': reader.total}


def _ca(path):
    with kit._regular(path, 2 << 20) as source: payload = source.read()
    try: value = payload.decode('ascii')
    except UnicodeError: raise ValueError('public CA must contain PEM certificates only') from None
    blocks = re.findall(r'-----BEGIN CERTIFICATE-----[\r\n]+[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----', value)
    remainder = value
    for block in blocks:
        ssl.PEM_cert_to_DER_cert(block); remainder = remainder.replace(block, '', 1)
    if not blocks or remainder.strip(): raise ValueError('public CA must contain PEM certificates only; private keys are forbidden')
    try: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=value)
    except ssl.SSLError: raise ValueError('public CA must contain valid X.509 certificates') from None
    return payload


def write_starters(root, catalog, base_url, *, ca_payload=b''):
    pins = {t['os'] + '-' + t['arch']: t['runner'] for t in catalog['targets']}
    common = {'@DOWNLOAD_HOST@': urlsplit(base_url).hostname, '@CATALOG_URL@': base_url + '/catalog.json', '@CATALOG_SHA256@': kit._sha(root / 'catalog.json'),
              '@CA_BASE64@': base64.b64encode(ca_payload).decode('ascii'), '@CA_SHA256@': hashlib.sha256(ca_payload).hexdigest() if ca_payload else ''}
    cases = []
    for key, ref in pins.items():
        if not key.startswith('windows-'):
            cases.append("    " + key + ") runner_url='" + base_url + '/' + ref['path'] + "'; runner_sha='" + ref['sha256'] + "'; runner_size='" + str(ref['size_bytes']) + "' ;;")
    common['@POSIX_TARGETS@'] = '\n'.join(cases)
    windows = {key.split('-')[1]: {'url': base_url + '/' + ref['path'], 'sha256': ref['sha256'], 'size_bytes': ref['size_bytes']} for key, ref in pins.items() if key.startswith('windows-')}
    common['@WINDOWS_TARGETS_BASE64@'] = base64.b64encode(json.dumps(windows, separators=(',', ':')).encode()).decode('ascii')
    for name in ('Connect.command', 'Connect.cmd', 'Connect.ps1'):
        content = (Path(__file__).parent / 'public' / (name + '.in')).read_text(encoding='utf-8')
        for key, value in common.items(): content = content.replace(key, value)
        if name.endswith('.cmd'):
            ps = (Path(__file__).parent / 'public' / 'Connect.ps1.in').read_text(encoding='utf-8')
            for key, value in common.items(): ps = ps.replace(key, value)
            content = content.replace('@POWERSHELL_BODY@', ps).replace('\n', '\r\n')
        kit._write(root / name, content, 0o755 if name.endswith('.command') else 0o644)


def prepare_release(*, binaries, profiles, out, endpoint, base_url, cpu_amd64_archive, cpu_arm64_archive, cuda_amd64_archive=None, ca_file=None):
    endpoint, base_url = _url(endpoint), _url(base_url, directory=True)
    if urlsplit(endpoint).netloc != urlsplit(base_url).netloc: raise ValueError('release assets and endpoint must share one HTTPS origin')
    binaries, profiles, out = (Path(os.path.abspath(os.fspath(p))) for p in (binaries, profiles, out))
    kit._no_links(out); kit._no_links(binaries)
    if os.path.lexists(out): raise FileExistsError(out)
    available = [(goos, arch, binaries / ('campus-' + goos + '-' + arch + ('.exe' if goos == 'windows' else ''))) for goos, arch in TARGETS]
    available = [t for t in available if os.path.lexists(t[2])]
    if not available: raise ValueError('no supported native runners supplied')
    for goos, arch, path in available:
        with kit._regular(path, 128 << 20) as source: _binary(source, goos, arch)
    ca_payload = _ca(Path(ca_file).absolute()) if ca_file else b''
    selections = {}
    for goos, arch, _ in available:
        selections[goos, arch] = [p for p in kit._profiles(profiles, arch) if p['accelerator'] == 'cpu' or (goos != 'darwin' and arch == 'amd64' and cuda_amd64_archive)]
    sources = {('amd64', 'cpu'): cpu_amd64_archive, ('arm64', 'cpu'): cpu_arm64_archive, ('amd64', 'cuda'): cuda_amd64_archive}
    out.parent.mkdir(parents=True, exist_ok=True); kit._no_links(out); parent = out.parent.resolve(strict=True)
    staging = Path(tempfile.mkdtemp(prefix='.campus-public-', dir=parent))
    try:
        kit._private(staging)
        for name in ('images', 'runners', 'profiles'): (staging / name).mkdir()
        image_refs = {}
        for selected in selections.values():
            for profile in selected:
                key = (profile['platform'].split('/')[1], profile['accelerator'])
                if key in image_refs: continue
                source_path = Path(os.path.abspath(os.fspath(sources[key])))
                destination = staging / 'images' / (key[1] + '-' + key[0] + '.tar.gz')
                with kit._regular(source_path, kit.MAX_IMAGE) as source:
                    before = os.fstat(source.fileno()); ref = _archive_ref(source, profile); after = os.fstat(source.fileno())
                    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                        raise ValueError('image source changed during verification')
                    os.link(source_path, destination)
                    linked = destination.stat()
                    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (linked.st_dev, linked.st_ino, linked.st_size, linked.st_mtime_ns):
                        raise ValueError('image source changed before hardlink publication')
                image_refs[key] = {'path': destination.relative_to(staging).as_posix(), **ref,
                                   **{k: profile[k] for k in ('image_digest', 'accelerator', 'platform')}}
        catalog = {'schema_version': '1', 'endpoint': endpoint, 'targets': []}
        if ca_payload:
            kit._write(staging / 'ca.pem', ca_payload, 0o644); catalog['ca_file'] = kit._file_ref(staging, staging / 'ca.pem')
        for goos, arch, path in available:
            name = path.name; destination = staging / 'runners' / name
            with kit._regular(path, 128 << 20) as source:
                _binary(source, goos, arch); expected = kit._source_sha(source)
                with destination.open('xb') as output: shutil.copyfileobj(source, output, length=CHUNK)
            destination.chmod(0o755)
            if kit._sha(destination) != expected: raise ValueError('runner changed during release preparation')
            selected = selections[goos, arch]; profile_path = staging / 'profiles' / (goos + '-' + arch + '.json')
            kit._write(profile_path, json.dumps({'schema_version': '1', 'profiles': selected}, indent=2) + '\n', 0o644)
            catalog['targets'].append({'os': goos, 'arch': arch, 'runner': kit._file_ref(staging, destination), 'profiles': kit._file_ref(staging, profile_path),
                                       'images': [image_refs[(arch, p['accelerator'])] for p in selected]})
        kit._write(staging / 'catalog.json', json.dumps(catalog, indent=2) + '\n', 0o644)
        if (staging / 'catalog.json').stat().st_size > 65536: raise ValueError('public catalog exceeds bound')
        write_starters(staging, catalog, base_url, ca_payload=ca_payload)
        kit._write(staging / 'README.txt', 'Campus public starter\n\nWindows: open Connect.cmd. macOS: run sh ./Connect.command in Terminal.\nLinux: run sh ./Connect.command. The starter selects your native client,\nchecks its size and SHA-256, then opens the visible Campus installer.\nDocker must already be installed and running. No autostart is installed.\nCPU is the default. CUDA requires detected NVIDIA support and explicit consent\nbefore any CUDA environment download. Paste your invitation separately when asked.\nThe optional public CA is used only by this private cache, never system trust.\nOn Windows only, custom-CA downloads use curl --ssl-revoke-best-effort, which\nallows unavailable/offline CRL checks while retaining certificate and hostname\nverification. Public PKI downloads retain curl default revocation behavior.\nDo not remove quarantine or disable OS security; use the trusted Terminal route\nabove and the normal per-file operating system approval if required.\n\nCatalog SHA-256: ' + kit._sha(staging / 'catalog.json') + '\n', 0o644)
        refs = {ref['path']: ref for target in catalog['targets'] for ref in [target['runner'], target['profiles'], *target['images']]}
        if ca_payload: refs['ca.pem'] = catalog['ca_file']
        for name in ('catalog.json', 'Connect.cmd', 'Connect.ps1', 'Connect.command', 'README.txt'): refs[name] = kit._file_ref(staging, staging / name)
        kit._write(staging / 'SHA256SUMS', ''.join(refs[name]['sha256'] + '  ' + name + '\n' for name in sorted(refs)), 0o644)
        kit._no_links(out); kit._publish(staging, out)
        return out
    finally:
        if os.path.lexists(staging):
            if not kit._linked(staging.lstat()) and staging.resolve(strict=True).parent == parent and staging.name.startswith('.campus-public-'): shutil.rmtree(staging)
            else: raise RuntimeError('refusing cleanup outside release staging')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('binaries', 'profiles', 'cpu-amd64-archive', 'cpu-arm64-archive', 'endpoint', 'base-url', 'out'): parser.add_argument('--' + name, required=True)
    parser.add_argument('--cuda-amd64-archive'); parser.add_argument('--ca-file')
    args = parser.parse_args(argv)
    try: out = prepare_release(**vars(args))
    except (OSError, ValueError, RuntimeError) as error: parser.exit(1, 'Public release preparation failed: ' + str(error) + '\n')
    print(json.dumps({'directory': str(out), 'catalog_sha256': kit._sha(out / 'catalog.json')}, ensure_ascii=False))
    return 0

if __name__ == '__main__': raise SystemExit(main())
