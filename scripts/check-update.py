#!/usr/bin/env python3
"""Preview or apply authenticated upstream releases to an Arch package."""
from __future__ import annotations

import argparse
import difflib
import fcntl
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from upstream import (DEPENDENCIES, fetch_package, inspect_payload, run,
                      unpack_deb, version_tuple)

LOCAL_SOURCES = ('claude-desktop.sh', 'upstream.py',
                 'package-assets.py', 'upstream.json', 'LICENSE.md')


def json_text(value: dict) -> str:
    """Serialize manifests consistently for checksums and readable review diffs."""
    return json.dumps(value, indent=2, sort_keys=True) + '\n'


def review_reasons(previous: dict, candidate: dict) -> list[str]:
    """Identify compatibility changes that require explicit human review."""
    reasons = []
    for field in ('dependencies', 'resources', 'native_modules', 'asar_main'):
        if previous.get(field) != candidate.get(field):
            reasons.append(f'{field} changed')
    if previous['electron'].split('.')[0] != candidate['electron'].split('.')[0]:
        reasons.append('Electron major changed')
    return reasons


def replace_assignment(text: str, name: str, value: str) -> str:
    """Replace one known literal assignment without sourcing package shell code."""
    text, count = re.subn(r'^' + re.escape(name) + r'=.*$', f'{name}={value}', text, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f'Expected exactly one {name} assignment')
    return text


def render_pkgbuild(root: pathlib.Path, entry: dict, manifest: dict) -> str:
    """Update fixed version/hash fields and checksums of all local build sources."""
    text = (root / 'PKGBUILD').read_text()
    text = replace_assignment(text, 'pkgver', entry['Version'])
    text = replace_assignment(text, 'pkgrel', '1')
    text = replace_assignment(text, '_upstream_sha256', repr(entry['SHA256']))
    sums = []
    for name in LOCAL_SOURCES:
        content = json_text(manifest).encode() if name == 'upstream.json' else (root / name).read_bytes()
        sums.append(hashlib.sha256(content).hexdigest())
    replacement = 'sha256sums=(\n' + ''.join(f"  '{digest}'\n" for digest in sums) + '  "$_upstream_sha256"\n)'
    text, count = re.subn(r'^sha256sums=\(\n.*?^\)', lambda _: replacement, text, flags=re.MULTILINE | re.DOTALL)
    if count != 1:
        raise ValueError('Expected one generated checksum block')
    return text


def generate_srcinfo(root: pathlib.Path, pkgbuild: str, manifest: dict) -> str:
    """Generate SRCINFO from a staged trusted PKGBUILD before changing live files."""
    with tempfile.TemporaryDirectory(prefix='claude-srcinfo-') as tmp:
        stage = pathlib.Path(tmp)
        (stage / 'PKGBUILD').write_text(pkgbuild)
        for name in LOCAL_SOURCES:
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if name == 'upstream.json':
                destination.write_text(json_text(manifest))
            else:
                shutil.copyfile(root / name, destination)
        import subprocess
        result = subprocess.run(['makepkg', '--printsrcinfo'], cwd=stage, text=True, capture_output=True)
        if result.returncode or not result.stdout.startswith('pkgbase = '):
            raise ValueError(f'SRCINFO generation failed: {result.stderr.strip()}')
        return result.stdout


def commit_update(root: pathlib.Path, pkgbuild: str, manifest: dict) -> pathlib.Path:
    """Stage all files, preserve a backup, replace atomically per file, and roll back errors."""
    srcinfo = generate_srcinfo(root, pkgbuild, manifest)
    contents = {'PKGBUILD': pkgbuild, 'upstream.json': json_text(manifest), '.SRCINFO': srcinfo}
    cache = root / '.cache'
    cache.mkdir(exist_ok=True)
    backup = cache / 'backups' / uuid.uuid4().hex
    backup.mkdir(parents=True)
    pending = root / '.update-transaction.json'
    if pending.exists():
        raise ValueError('An interrupted update needs manual restoration before continuing')
    replaced = []
    with tempfile.TemporaryDirectory(prefix='update-', dir=cache) as tmp:
        stage = pathlib.Path(tmp)
        for name, text in contents.items():
            shutil.copyfile(root / name, backup / name)
            (stage / name).write_text(text)
        pending.write_text(json_text({'backup': str(backup.relative_to(root))}))
        try:
            for name in contents:
                os.replace(stage / name, root / name)
                replaced.append(name)
        except BaseException:
            # Restore only files actually replaced; retained backups also cover a crash.
            for name in reversed(replaced):
                restore = stage / ('restore-' + name)
                shutil.copyfile(backup / name, restore)
                os.replace(restore, root / name)
            pending.unlink()
            raise
        pending.unlink()
    return backup


def check(root: pathlib.Path, apply: bool, reviewed: bool, version: str | None) -> int:
    """Authenticate a candidate and expose compatibility changes before optional writes."""
    if (root / '.update-transaction.json').exists():
        raise ValueError('Interrupted update: restore the backup named in .update-transaction.json first')
    baseline = json.loads((root / 'upstream.json').read_text())
    text = (root / 'PKGBUILD').read_text()
    versions = re.findall(r'^pkgver=([0-9.]+)$', text, re.MULTILINE)
    digests = re.findall(r"^_upstream_sha256='([0-9a-f]{64})'$", text, re.MULTILINE)
    if versions != [baseline['package']['Version']] or digests != [baseline['package']['SHA256']]:
        raise ValueError('PKGBUILD and inspection baseline disagree')
    cache = root / '.cache'
    if cache.is_symlink():
        raise ValueError('Cache must be a real directory')
    cache.mkdir(exist_ok=True)
    entry, asset = fetch_package(root / 'keys/anthropic.asc', cache, version)
    if version_tuple(entry['Version']) < version_tuple(versions[0]):
        raise ValueError('Upstream downgrade rejected; use a retained package for rollback')
    with tempfile.TemporaryDirectory(prefix='inspect-', dir=cache) as tmp:
        payload, control = unpack_deb(asset, pathlib.Path(tmp))
        if control['Version'] != entry['Version'] or any(control.get(field, '') != entry.get(field, '') for field in DEPENDENCIES):
            raise ValueError('Debian control metadata disagrees with authenticated index')
        inspection = inspect_payload(payload, control)
    manifest = {'schema': 1, 'package': {field: entry[field] for field in ('Version', 'Architecture', 'Filename', 'Size', 'SHA256')}, 'inspection': inspection}
    if entry['Version'] == versions[0]:
        if manifest != baseline:
            raise ValueError('Same-version upstream asset or inspection baseline changed')
        print(f'Up to date: {versions[0]} (signature, index, asset, and layout verified)')
        return 3
    reasons = review_reasons(baseline['inspection'], inspection)
    print(f"Candidate: {versions[0]} -> {entry['Version']}; Electron {inspection['electron']}; Chromium {inspection['chromium']}")
    print(f"SHA256: {entry['SHA256']}")
    if reasons:
        print('Review required: ' + '; '.join(reasons))
        print(''.join(difflib.unified_diff(json_text(baseline['inspection']).splitlines(True), json_text(inspection).splitlines(True), fromfile='previous inspection', tofile='candidate inspection')), end='')
    pkgbuild = render_pkgbuild(root, entry, manifest)
    print(''.join(difflib.unified_diff(text.splitlines(True), pkgbuild.splitlines(True), fromfile='PKGBUILD', tofile='candidate PKGBUILD')), end='')
    if reasons and not reviewed:
        return 4
    if apply:
        backup = commit_update(root, pkgbuild, manifest)
        print(f'Applied update. Previous files retained in {backup.relative_to(root)}')
    else:
        print('Preview only; package configuration was not changed.')
    return 0


def main() -> int:
    """Serialize updater runs and map expected failures to documented exit codes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='write an authenticated candidate after showing its diff')
    parser.add_argument('--reviewed', action='store_true', help='acknowledge compatibility changes previously inspected')
    parser.add_argument('--version', help='select a specific numeric upstream release')
    args = parser.parse_args()
    if args.reviewed and not args.apply:
        parser.error('--reviewed requires --apply')
    if args.version:
        version_tuple(args.version)
    root = pathlib.Path(__file__).resolve().parents[1]
    descriptor = os.open(root / '.update.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Another update check is running') from exc
        return check(root, args.apply, args.reviewed, args.version)
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
