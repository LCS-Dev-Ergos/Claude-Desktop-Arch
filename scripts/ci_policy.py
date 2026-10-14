"""Validate package data and release gates without executing a package recipe."""
import argparse
import hashlib
import json
import pathlib
import re

LOCAL_SOURCES = ('claude-desktop.sh', 'upstream.py', 'package-assets.py', 'upstream.json', 'LICENSE.md')
PUBLISH_FILES = ('PKGBUILD', '.SRCINFO', *LOCAL_SOURCES)
KNOWN_LINK_ERRORS = {
    'Symlink (usr/libexec/virtiofsd) points to non-existing ../lib/virtiofsd',
    'Symlink (usr/share/edk2/OVMF_CODE_4M.fd) points to non-existing x64/OVMF_CODE.4m.fd',
    'Symlink (usr/share/edk2/OVMF_VARS_4M.fd) points to non-existing x64/OVMF_VARS.4m.fd',
}


def update_status(code: int) -> str:
    """Preserve the updater's candidate, no-change, and human-review outcomes."""
    states = {0: 'candidate', 3: 'current', 4: 'review_required'}
    if code not in states:
        raise ValueError(f'Upstream verification failed with exit code {code}')
    return states[code]


def check_namcap(text: str) -> None:
    """Reject every new error while retaining the exact optional-link exceptions."""
    errors = [line.split(' E: ', 1)[1] for line in text.splitlines() if ' E: ' in line]
    unexpected = [message for message in errors if message not in KNOWN_LINK_ERRORS]
    if unexpected:
        raise ValueError('Unreviewed Namcap errors: ' + '; '.join(unexpected))


def check_environment(config: dict, policies: dict) -> None:
    """Require a real reviewer gate and a deployment policy restricted to main only."""
    rules = config.get('protection_rules', [])
    if not any(rule.get('type') == 'required_reviewers' and rule.get('reviewers') for rule in rules):
        raise ValueError('The aur environment must have required reviewers')
    policy = config.get('deployment_branch_policy') or {}
    branches = policies.get('branch_policies', [])
    if not policy.get('custom_branch_policies') or policy.get('protected_branches'):
        raise ValueError('Configure an explicit main-only environment branch policy')
    if len(branches) != 1 or branches[0].get('name') != 'main' or branches[0].get('type') != 'branch':
        raise ValueError('The aur environment must allow exactly the main branch, with no tag policies')
    if policies.get('total_count', 1) != 1:
        raise ValueError('Unexpected additional deployment policies')


def metadata(text: str) -> dict:
    """Parse the supported single-package SRCINFO identity and source/checksum lists."""
    if len(text.encode()) > 256 * 1024:
        raise ValueError('SRCINFO exceeds the size limit')
    fields = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.strip().partition(' = ')
        if not separator:
            raise ValueError('Malformed SRCINFO field')
        fields.setdefault(key, []).append(value)
    for name in ('pkgbase', 'pkgname', 'pkgver', 'pkgrel'):
        if len(fields.get(name, [])) != 1:
            raise ValueError(f'Expected one {name}')
    if fields['pkgbase'] != ['claude-desktop-sandboxed'] or fields['pkgname'] != ['claude-desktop-sandboxed']:
        raise ValueError('Unexpected AUR package identity')
    version, release = fields['pkgver'][0], fields['pkgrel'][0]
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version) or not re.fullmatch(r'[1-9][0-9]*', release):
        raise ValueError('Unsupported package version or release')
    if fields.get('epoch') or fields.get('arch') != ['x86_64']:
        raise ValueError('Unsupported epoch or architecture')
    expected_asset = ('https://downloads.claude.ai/claude-desktop/apt/stable/pool/main/c/claude-desktop/'
                      f'claude-desktop_{version}_amd64.deb')
    if fields.get('source') != [*LOCAL_SOURCES, expected_asset]:
        raise ValueError('Package sources differ from the reviewed publication allowlist')
    hashes = fields.get('sha256sums', [])
    if len(hashes) != 6 or not all(re.fullmatch(r'[0-9a-f]{64}', value) for value in hashes):
        raise ValueError('Every source must have a strict SHA-256')
    return {'version': version, 'release': int(release),
            'key': (*map(int, version.split('.')), int(release)), 'hashes': hashes}


def snapshot(root: pathlib.Path) -> dict:
    """Validate a bounded, regular, checksum-matching package source snapshot."""
    if (root / '.update-transaction.json').exists():
        raise ValueError('Interrupted update must be recovered before publication')
    for name in PUBLISH_FILES:
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
            raise ValueError(f'Publication input must be a bounded regular file: {name}')
    result = metadata((root / '.SRCINFO').read_text())
    for name, digest in zip(LOCAL_SOURCES, result['hashes'], strict=False):
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f'Publication checksum mismatch: {name}')
    baseline = json.loads((root / 'upstream.json').read_text())['package']
    if baseline['Version'] != result['version'] or baseline['SHA256'] != result['hashes'][-1]:
        raise ValueError('SRCINFO and authenticated inspection baseline disagree')
    return result


def check_transition(new: tuple, old: tuple, identical: bool) -> None:
    """Reject downgraded releases and changed bytes under an unchanged package version."""
    if new < old or (new == old and not identical):
        raise ValueError('AUR downgrade or changed same-version publication rejected; increment pkgrel')


def main() -> None:
    """Expose the offline snapshot, lint, and environment gates to workflow steps."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('snapshot')
    check.add_argument('root', type=pathlib.Path)
    lint = commands.add_parser('namcap')
    lint.add_argument('log', type=pathlib.Path)
    environment = commands.add_parser('environment')
    environment.add_argument('config', type=pathlib.Path)
    environment.add_argument('policies', type=pathlib.Path)
    args = parser.parse_args()
    if args.command == 'snapshot':
        print(json.dumps(snapshot(args.root)))
    elif args.command == 'namcap':
        check_namcap(args.log.read_text())
    else:
        check_environment(json.loads(args.config.read_text()), json.loads(args.policies.read_text()))


if __name__ == '__main__':
    main()
