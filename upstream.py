"""Authenticate official APT assets and validate Debian payloads before use."""
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import json
import mmap
import pathlib
import posixpath
import re
import struct
import subprocess
import tarfile
import tempfile

REPO = 'https://downloads.claude.ai/claude-desktop/apt/stable/'
FINGERPRINT = '31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE'
DEPENDENCIES = ('Pre-Depends', 'Depends', 'Recommends', 'Suggests')
MAX_ASSET = 1024 * 1024 * 1024
MAX_EXPANDED = 2 * MAX_ASSET


def run(args: list[str]) -> str:
    """Run a checked command without a shell and retain useful diagnostics."""
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError(f'{args[0]} failed: {result.stderr.strip()}')
    return result.stdout


def download(relative: str, target: pathlib.Path, limit: int) -> None:
    """Download one bounded HTTPS asset without following external redirects."""
    if relative.startswith('/') or '..' in relative.split('/') or not re.fullmatch(r'[A-Za-z0-9_./+-]+', relative):
        raise ValueError('Invalid upstream asset path')
    run(['curl', '--fail', '--silent', '--show-error', '--proto', '=https',
         '--tlsv1.2', '--connect-timeout', '20', '--max-time', '300',
         '--max-filesize', str(limit), '--output', str(target), REPO + relative])
    if target.stat().st_size > limit:
        raise ValueError('Download exceeded its size limit')


def parse_control(text: str) -> dict[str, str]:
    """Parse one Debian control stanza, including continuation lines."""
    fields: dict[str, str] = {}
    previous = None
    for line in text.splitlines():
        if not line:
            continue
        if line[0].isspace():
            if previous is None:
                raise ValueError('Unexpected control continuation')
            fields[previous] += '\n' + line[1:]
            continue
        name, separator, value = line.partition(':')
        if not separator or not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]*', name) or name in fields:
            raise ValueError('Invalid or duplicate control field')
        fields[name] = value.strip()
        previous = name
    return fields


def parse_release(text: str, now: dt.datetime | None = None) -> dict[str, tuple[str, int]]:
    """Enforce signed freshness and parse the SHA-256 index table."""
    fields = parse_control(text)
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        date = email.utils.parsedate_to_datetime(fields['Date'])
        expiry = email.utils.parsedate_to_datetime(fields['Valid-Until'])
        if date.tzinfo is None or expiry.tzinfo is None:
            raise ValueError('Release timestamps must specify a timezone')
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError('Missing or invalid release timestamps') from exc
    if date > now + dt.timedelta(minutes=10) or expiry <= now or expiry <= date or expiry - date > dt.timedelta(days=14):
        raise ValueError('Expired or implausibly dated release metadata')
    indexes = {}
    for row in fields.get('SHA256', '').splitlines():
        if not row.strip():
            continue
        values = row.split()
        if len(values) != 3:
            raise ValueError('Invalid SHA256 release entry')
        digest, size, path = values
        if not re.fullmatch(r'[0-9a-f]{64}', digest) or not size.isdigit() or path in indexes:
            raise ValueError('Invalid or duplicate SHA256 release entry')
        indexes[path] = (digest, int(size))
    if not indexes:
        raise ValueError('Release contains no SHA256 indexes')
    return indexes


def verify_release(signed: pathlib.Path, key: pathlib.Path,
                   fingerprint: str = FINGERPRINT) -> str:
    """Authenticate InRelease using only the pinned primary key in an isolated home."""
    with tempfile.TemporaryDirectory(prefix='claude-signature-') as tmp:
        home = pathlib.Path(tmp)
        info = run(['gpg', '--no-options', '--homedir', tmp, '--batch', '--with-colons', '--show-keys', str(key)])
        primary = []
        want_fingerprint = False
        for line in info.splitlines():
            fields = line.split(':')
            if fields[0] == 'pub':
                want_fingerprint = True
            elif fields[0] == 'fpr' and want_fingerprint:
                primary.append(fields[9]); want_fingerprint = False
        if primary != [fingerprint]:
            raise ValueError('Unexpected upstream signing key; review key rotation manually')
        keyring = home / 'key.gpg'
        run(['gpg', '--no-options', '--homedir', tmp, '--batch', '--dearmor', '--output', str(keyring), str(key)])
        plaintext = home / 'Release'
        status = run(['gpgv', '--homedir', tmp, '--keyring', str(keyring), '--status-fd', '1', '--output', str(plaintext), str(signed)])
        signatures = [line.split()[2:] for line in status.splitlines() if line.startswith('[GNUPG:] VALIDSIG ')]
        if len(signatures) != 1 or len(signatures[0]) < 9:
            raise ValueError('Missing or ambiguous valid release signature')
        signature = signatures[0]
        primary_signer = signature[9] if len(signature) > 9 else signature[0]
        if primary_signer != fingerprint or signature[7] not in ('8', '9', '10'):
            raise ValueError('Unapproved signing key or weak signature digest')
        return plaintext.read_text()


def verify_file(path: pathlib.Path, size: int, digest: str) -> None:
    """Require both the authenticated length and SHA-256 of a downloaded file."""
    if not re.fullmatch(r'[0-9a-f]{64}', digest) or path.stat().st_size != size:
        raise ValueError(f'Invalid hash or size mismatch: {path.name}')
    hasher = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            hasher.update(block)
    if hasher.hexdigest() != digest:
        raise ValueError(f'SHA256 mismatch: {path.name}')


def version_tuple(version: str) -> tuple[int, ...]:
    """Accept the upstream numeric version format without executable syntax."""
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError(f'Unsupported upstream version: {version!r}')
    return tuple(map(int, version.split('.')))


def select_package(index: str, architecture: str, version: str | None = None) -> dict[str, str]:
    """Select only Claude assets with exact package, architecture, and pool identity."""
    entries = []
    for stanza in re.split(r'\n\s*\n', index.strip()):
        fields = parse_control(stanza)
        if fields.get('Package') != 'claude-desktop' or fields.get('Architecture') != architecture:
            continue
        candidate = fields.get('Version', '')
        version_tuple(candidate)
        expected = f'pool/main/c/claude-desktop/claude-desktop_{candidate}_{architecture}.deb'
        if fields.get('Filename') != expected or not re.fullmatch(r'[0-9a-f]{64}', fields.get('SHA256', '')):
            raise ValueError('Invalid package filename or checksum')
        size = fields.get('Size', '')
        if not size.isdigit() or not 0 < int(size) <= MAX_ASSET:
            raise ValueError('Invalid package size')
        entries.append(fields)
    if len({entry['Version'] for entry in entries}) != len(entries):
        raise ValueError('Duplicate package versions')
    if version is not None:
        entries = [entry for entry in entries if entry['Version'] == version]
    if not entries:
        raise ValueError('Requested Claude version/architecture is absent from index')
    return max(entries, key=lambda entry: version_tuple(entry['Version']))


def fetch_package(key: pathlib.Path, work: pathlib.Path,
                  version: str | None = None) -> tuple[dict[str, str], pathlib.Path]:
    """Authenticate the current index and download or reverify a versioned amd64 asset."""
    signed = work / 'InRelease'
    download('dists/stable/InRelease', signed, 1024 * 1024)
    indexes = parse_release(verify_release(signed, key))
    path = 'main/binary-amd64/Packages'
    digest, size = indexes[path]
    if not 0 < size <= 16 * 1024 * 1024:
        raise ValueError('Unexpected Packages index size')
    index = work / 'Packages'
    download('dists/stable/' + path, index, size)
    verify_file(index, size, digest)
    entry = select_package(index.read_text(), 'amd64', version)
    asset = work / pathlib.PurePosixPath(entry['Filename']).name
    if not asset.exists():
        partial = asset.with_suffix('.partial')
        try:
            download(entry['Filename'], partial, int(entry['Size']))
            verify_file(partial, int(entry['Size']), entry['SHA256'])
            partial.replace(asset)
        finally:
            partial.unlink(missing_ok=True)
    verify_file(asset, int(entry['Size']), entry['SHA256'])
    return entry, asset


def validate_tar(path: pathlib.Path) -> list[tarfile.TarInfo]:
    """Reject traversal, duplicate names, special files, hardlinks, and escaping symlinks."""
    with tarfile.open(path) as archive:
        members = archive.getmembers()
    if len(members) > 100_000 or sum(item.size for item in members) > MAX_EXPANDED:
        raise ValueError('Archive exceeds payload limits')
    names = set()
    for item in members:
        name = item.name
        if name.startswith('/') or '..' in pathlib.PurePosixPath(name).parts:
            raise ValueError('Unsafe archive path')
        normalized = posixpath.normpath(name)
        if normalized == '.' and item.isdir():
            continue
        if normalized in names:
            raise ValueError('Duplicate archive path')
        names.add(normalized)
        if not (item.isfile() or item.isdir() or item.issym()):
            raise ValueError('Unsupported archive member type')
        if item.issym():
            target = posixpath.normpath(posixpath.join(posixpath.dirname(normalized), item.linkname))
            if item.linkname.startswith('/') or target == '..' or target.startswith('../'):
                raise ValueError('Unsafe archive symlink')
    return members


def unpack_deb(asset: pathlib.Path, work: pathlib.Path) -> tuple[pathlib.Path, dict[str, str]]:
    """Extract authenticated Debian archives without running their maintainer scripts."""
    listing = run(['bsdtar', '-tf', str(asset)]).splitlines()
    controls = [name for name in listing if re.fullmatch(r'control\.tar\.(xz|gz|zst)', name)]
    payloads = [name for name in listing if re.fullmatch(r'data\.tar\.(xz|gz|zst)', name)]
    if len(listing) != 3 or len(controls) != 1 or len(payloads) != 1 or 'debian-binary' not in listing:
        raise ValueError('Unexpected Debian archive layout')
    for name in [controls[0], payloads[0]]:
        target = work / name
        with target.open('wb') as output:
            subprocess.run(['bsdtar', '-xOf', str(asset), name], stdout=output, check=True)
        validate_tar(target)
    with tarfile.open(work / controls[0]) as archive:
        control_members = [item for item in archive if posixpath.normpath(item.name) == 'control' and item.isfile()]
        if len(control_members) != 1:
            raise ValueError('Missing Debian control file')
        control = parse_control(archive.extractfile(control_members[0]).read().decode())
    payload = work / 'payload'
    payload.mkdir()
    with tarfile.open(work / payloads[0]) as archive:
        archive.extractall(payload, filter='data')
    return payload, control


def asar_package(path: pathlib.Path) -> dict:
    """Read only package.json from ASAR with bounded header and content offsets."""
    with path.open('rb') as archive:
        prefix = archive.read(16)
        if len(prefix) != 16:
            raise ValueError('Truncated ASAR')
        _, header_size, _, length = struct.unpack('<4I', prefix)
        if not 0 < length <= 64 * 1024 * 1024 or length + 8 > header_size:
            raise ValueError('Invalid ASAR header length')
        header = json.loads(archive.read(length))
        entry = header['files']['package.json']
        size = entry['size']; offset = 8 + header_size + int(entry['offset'])
        if not 0 < size <= 1024 * 1024 or offset < 8 + header_size or offset + size > path.stat().st_size:
            raise ValueError('Invalid ASAR package metadata')
        archive.seek(offset)
        return json.loads(archive.read(size))


def inspect_payload(payload: pathlib.Path, control: dict[str, str]) -> dict:
    """Record identity, dependencies, resource layout, native modules, and bundled runtime."""
    app = payload / 'usr/lib/claude-desktop'
    resources = app / 'resources'
    for name in ['claude-desktop', 'chrome-sandbox', 'resources/app.asar']:
        if not (app / name).is_file() or (app / name).is_symlink():
            raise ValueError(f'Missing regular application asset: {name}')
    if control.get('Package') != 'claude-desktop' or control.get('Architecture') != 'amd64':
        raise ValueError('Unexpected Debian package identity')
    meta = asar_package(resources / 'app.asar')
    if meta.get('version') != control.get('Version') or meta.get('main') != '.vite/build/index.pre.js':
        raise ValueError('Unexpected ASAR version or entry point')
    with (app / 'claude-desktop').open('rb') as binary, mmap.mmap(binary.fileno(), 0, access=mmap.ACCESS_READ) as data:
        match = re.search(rb'Chrome/([0-9.]+) Electron/([0-9.]+)', data)
        if not match:
            raise ValueError('Cannot identify bundled runtime; manual inspection required')
        chromium, electron = (value.decode() for value in match.groups())
    return {'dependencies': {name: control.get(name, '') for name in DEPENDENCIES},
            'resources': sorted(path.name for path in resources.iterdir()),
            'native_modules': sorted(str(path.relative_to(resources)) for path in resources.rglob('*.node')),
            'electron': electron, 'chromium': chromium, 'asar_main': meta['main']}
