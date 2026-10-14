#!/usr/bin/env python3
"""Prepare only reviewed application assets from a checksum-verified official Debian file."""
import argparse
import json
import pathlib
import shutil
import tempfile

from upstream import DEPENDENCIES, inspect_payload, unpack_deb, verify_file


def main() -> None:
    """Stage a validated payload and strip privilege bits before makepkg installs it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('asset', type=pathlib.Path)
    parser.add_argument('manifest', type=pathlib.Path)
    parser.add_argument('destination', type=pathlib.Path)
    args = parser.parse_args()
    baseline = json.loads(args.manifest.read_text())
    if baseline.get('schema') != 1:
        raise ValueError('Unsupported inspection manifest schema')
    expected = baseline['package']
    verify_file(args.asset, int(expected['Size']), expected['SHA256'])
    with tempfile.TemporaryDirectory(prefix='claude-assets-') as tmp:
        payload, control = unpack_deb(args.asset, pathlib.Path(tmp))
        if control['Version'] != expected['Version'] or inspect_payload(payload, control) != baseline['inspection']:
            raise ValueError('Payload differs from the reviewed inspection baseline')
        if args.destination.exists():
            raise ValueError('Staging destination already exists')
        args.destination.mkdir()
        selected = ['usr/lib/claude-desktop', 'usr/share/icons/hicolor',
                    'usr/share/applications/com.anthropic.Claude.desktop',
                    'usr/share/doc/claude-desktop/copyright']
        for name in selected:
            source = payload / name
            destination = args.destination / name
            if not source.exists() or source.is_symlink():
                raise ValueError(f'Missing selected asset: {name}')
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copyfile(source, destination)
                destination.chmod(source.stat().st_mode & 0o777)
        for path in args.destination.rglob('*'):
            if path.is_file():
                path.chmod(path.stat().st_mode & 0o777)
        # Keep no duplicate setuid helper: the runtime must use user namespaces.
        (args.destination / 'usr/lib/claude-desktop/chrome-sandbox').unlink()


if __name__ == '__main__':
    main()
