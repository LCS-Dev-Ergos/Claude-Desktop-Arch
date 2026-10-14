#!/usr/bin/env python3
"""Create a manual, document-only draft PR to verify the dedicated update token."""
import json
import os
import pathlib
import re
import subprocess

from ci_policy import PUBLISH_FILES, snapshot

DOCUMENT = 'docs/Upstream-PR-Token-Test.md'


def git(root: pathlib.Path, *arguments: str) -> str:
    """Run Git without hooks, commit signing, or inherited global configuration."""
    return subprocess.check_output(
        ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
         '-C', str(root), *arguments], text=True,
        env={**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull})


def validate_context(env: dict, checkout: str) -> dict:
    """Restrict the credential test to a manual invocation of the exact main commit."""
    sha, run_id, repository = (env.get(name, '') for name in
                               ('GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_REPOSITORY'))
    if env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch' or env.get('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('Token tests require a manual main invocation')
    if not re.fullmatch(r'[0-9a-f]{40}', sha) or sha != checkout:
        raise ValueError('Checkout must match the exact workflow commit')
    if not re.fullmatch(r'[1-9][0-9]{0,19}', run_id) or not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid workflow run or repository identity')
    if not env.get('GH_TOKEN'):
        raise ValueError('Configure UPSTREAM_PR_TOKEN before running the manual test')
    return {'sha': sha, 'run_id': run_id, 'repository': repository,
            'branch': 'automation/pr-token-smoke-' + run_id}


def write_document(root: pathlib.Path, version: str, env: dict) -> pathlib.Path:
    """Write one fixed test document, preserving existing files and package metadata."""
    directory, document = root / 'docs', root / DOCUMENT
    if directory.is_symlink() or document.exists() or document.is_symlink():
        raise ValueError('Refuse to replace existing test content or follow a symlink')
    directory.mkdir(exist_ok=True)
    document.write_text(
        '# PR Token Test\n\n'
        'This document records a manual credentials and workflow test. It is not an upstream release.\n\n'
        f"Pinned application version: `{version}`.\n\n"
        f"Source commit: `{env['GITHUB_SHA']}`.\n\n"
        f"[Authenticated inspection run](https://github.com/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}).\n\n"
        'The package recipe, sources, checksums, and release are unchanged. '
        'Verify that the normal PR validation runs, then close this draft without merging it.\n')
    return document


def prepare_commit(root: pathlib.Path, env: dict) -> tuple[str, pathlib.Path]:
    """Create a test branch whose only changed path is the bounded test document."""
    context = validate_context(env, git(root, 'rev-parse', 'HEAD').strip())
    package = snapshot(root)
    if git(root, 'status', '--porcelain').strip():
        raise ValueError('Token test requires a clean source checkout')
    git(root, 'switch', '-c', context['branch'])
    document = write_document(root, package['version'], env)
    git(root, 'add', '--', DOCUMENT)
    git(root, '-c', 'user.name=github-actions[bot]',
        '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
        'commit', '-m', 'test(ci): Verify upstream PR token permissions.')
    if git(root, 'diff', '--name-only', context['sha'], 'HEAD').splitlines() != [DOCUMENT]:
        raise ValueError('Token test must change only its document')
    return context['branch'], document


def main() -> None:
    """Open one draft test PR without overwriting branches or exposing the token value."""
    root = pathlib.Path(__file__).resolve().parents[1]
    context = validate_context(os.environ, git(root, 'rev-parse', 'HEAD').strip())
    repository, branch = context['repository'], context['branch']
    existing = json.loads(subprocess.check_output(
        ['gh', 'pr', 'list', '--repo', repository, '--head', branch, '--state', 'all', '--json', 'url'], text=True))
    if existing:
        print('This run already has a test PR:', existing[0]['url'])
        return
    remote = f'https://github.com/{repository}.git'
    credentials = ('-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential')
    if git(root, *credentials, 'ls-remote', remote, 'refs/heads/' + branch).strip():
        raise ValueError('Test branch exists without a PR; inspect it before retrying')
    branch, document = prepare_commit(root, os.environ)
    git(root, *credentials, 'push', remote, 'HEAD:refs/heads/' + branch)
    url = subprocess.check_output(
        ['gh', 'pr', 'create', '--repo', repository, '--base', 'main', '--head', branch, '--draft',
         '--title', '[TEST] Verify upstream PR token and CI triggers', '--body-file', str(document)], text=True).strip()
    print(url)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write(f'Created draft token test: {url}\n\nClose it after PR validation; do not merge.\n')


if __name__ == '__main__':
    main()
