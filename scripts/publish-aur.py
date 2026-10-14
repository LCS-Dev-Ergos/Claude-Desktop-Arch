#!/usr/bin/env python3
"""Publish validated package source files to the fixed AUR SSH endpoint."""
import argparse
import base64
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import tempfile

from ci_policy import PUBLISH_FILES, check_transition, metadata, snapshot


def git(directory: pathlib.Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run Git with hooks and inherited system/global configuration disabled."""
    environment = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}
    return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(directory), *arguments],
                          env=environment, capture_output=True, check=check)


def sync_package(source: pathlib.Path, remote: str, checkout: pathlib.Path, name: str, email: str) -> bool:
    """Mirror only reviewed files, rejecting remote surprises and non-forward releases.

    The remote argument permits disposable filesystem remotes in offline tests;
    the production entry point always derives the fixed AUR SSH URL below.
    """
    candidate = snapshot(source)
    if not name.strip() or any(ord(char) < 32 for char in name + email) or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+', email):
        raise ValueError('AUR commit name and email must be configured')
    git(source, 'clone', '--no-checkout', '--no-tags', '--', remote, str(checkout))
    if git(checkout, 'symbolic-ref', 'HEAD').stdout.strip() != b'refs/heads/master':
        raise ValueError('Unexpected AUR default branch; expected master')
    head = git(checkout, 'rev-parse', '--verify', 'HEAD', check=False)
    if head.returncode == 0:
        entries = git(checkout, 'ls-tree', '-r', '-z', 'HEAD').stdout.split(b'\0')
        existing = set()
        for entry in filter(None, entries):
            attributes, path = entry.split(b'\t', 1)
            filename = path.decode()
            if filename not in PUBLISH_FILES or attributes.split()[0] not in (b'100644', b'100755'):
                raise ValueError('Unexpected AUR file or mode; inspect the remote before migration')
            existing.add(filename)
        if '.SRCINFO' not in existing:
            raise ValueError('Existing AUR repository has no SRCINFO')
        previous = metadata(git(checkout, 'show', 'HEAD:.SRCINFO').stdout.decode())
        identical = existing == set(PUBLISH_FILES) and all(
            git(checkout, 'show', 'HEAD:' + filename).stdout == (source / filename).read_bytes()
            for filename in PUBLISH_FILES)
        check_transition(candidate['key'], previous['key'], identical)
        if identical:
            return False
    elif git(checkout, 'for-each-ref', '--format=%(refname)').stdout.strip():
        raise ValueError('AUR HEAD is invalid but other refs exist')
    for filename in PUBLISH_FILES:
        shutil.copyfile(source / filename, checkout / filename)
    git(checkout, 'config', 'user.name', name)
    git(checkout, 'config', 'user.email', email)
    git(checkout, 'add', '--', *PUBLISH_FILES)
    git(checkout, 'commit', '-m', f"Update to {candidate['version']}-{candidate['release']}")
    git(checkout, 'push', '--porcelain', 'origin', 'HEAD:refs/heads/master')
    return True


def check_known_hosts(text: str) -> None:
    """Accept explicit AUR host-key pins only; forbid wildcard or CA trust."""
    lines = [line.split() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    if not lines:
        raise ValueError('Verified AUR_KNOWN_HOSTS is required')
    for fields in lines:
        if len(fields) < 3 or fields[0] != 'aur.archlinux.org' or fields[1] not in ('ssh-ed25519', 'ecdsa-sha2-nistp256', 'ssh-rsa'):
            raise ValueError('Known hosts must pin the exact aur.archlinux.org host')
        if len(base64.b64decode(fields[2], validate=True)) < 32:
            raise ValueError('Invalid SSH host key')


def main() -> None:
    """Use ephemeral protected credentials only after validating the checked-out main commit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=pathlib.Path, default=pathlib.Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    snapshot(root)
    expected = os.environ.get('GITHUB_SHA', '')
    if os.environ.get('GITHUB_REF') != 'refs/heads/main' or not re.fullmatch(r'[0-9a-f]{40}', expected):
        raise ValueError('AUR publication is restricted to an exact main commit')
    if git(root, 'rev-parse', 'HEAD').stdout.decode().strip() != expected:
        raise ValueError('Checkout does not match the validated workflow commit')
    known_hosts = os.environ.pop('AUR_KNOWN_HOSTS', '')
    private_key = os.environ.pop('AUR_SSH_PRIVATE_KEY', '')
    check_known_hosts(known_hosts)
    if not private_key.strip():
        raise ValueError('AUR_SSH_PRIVATE_KEY is required')
    with tempfile.TemporaryDirectory(prefix='aur-publish-') as temporary:
        work = pathlib.Path(temporary)
        identity, hosts = work / 'identity', work / 'known_hosts'
        identity.write_text(private_key.rstrip() + '\n')
        hosts.write_text(known_hosts)
        identity.chmod(0o600)
        hosts.chmod(0o600)
        os.environ['GIT_SSH_COMMAND'] = (
            f'ssh -F /dev/null -i {shlex.quote(str(identity))} -o IdentitiesOnly=yes '
            f'-o UserKnownHostsFile={shlex.quote(str(hosts))} -o GlobalKnownHostsFile=/dev/null '
            '-o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=20')
        try:
            changed = sync_package(root, 'ssh://aur@aur.archlinux.org/claude-desktop-sandboxed.git',
                                   work / 'checkout', os.environ.get('AUR_COMMIT_NAME', ''),
                                   os.environ.get('AUR_COMMIT_EMAIL', ''))
            print('AUR updated.' if changed else 'AUR already matches the validated source snapshot.')
        finally:
            os.environ.pop('GIT_SSH_COMMAND', None)


if __name__ == '__main__':
    main()
