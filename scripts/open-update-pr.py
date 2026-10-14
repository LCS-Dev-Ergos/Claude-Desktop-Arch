#!/usr/bin/env python3
"""Create an update PR from a bounded data artifact produced by this workflow run."""
import argparse
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

from ci_policy import PUBLISH_FILES, snapshot

UPDATE_FILES = ('PKGBUILD', '.SRCINFO', 'upstream.json')


def load_updater(root: pathlib.Path):
    """Load the trusted checkout's renderer, never code from the candidate artifact."""
    spec = importlib.util.spec_from_file_location('trusted_updater', root / 'scripts/check-update.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_candidate(root: pathlib.Path, candidate: pathlib.Path) -> dict:
    """Require an ordinary forward update containing only canonical recipe changes."""
    snapshot(root)
    expected = {*UPDATE_FILES, 'report.md'}
    if {path.name for path in candidate.iterdir()} != expected:
        raise ValueError('Unexpected update artifact entries')
    for name in expected:
        path = candidate / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
            raise ValueError('Update artifact must contain bounded regular files')
    updater = load_updater(root)
    previous = json.loads((root / 'upstream.json').read_text())
    manifest = json.loads((candidate / 'upstream.json').read_text())
    entry = manifest['package']
    if updater.version_tuple(entry['Version']) <= updater.version_tuple(previous['package']['Version']):
        raise ValueError('Automated PRs require a new upstream version')
    if updater.review_reasons(previous['inspection'], manifest['inspection']):
        raise ValueError('Compatibility changes require manual preparation and review')
    if (candidate / 'PKGBUILD').read_text() != updater.render_pkgbuild(root, entry, manifest):
        raise ValueError('Candidate recipe contains changes beyond the trusted update renderer')
    with tempfile.TemporaryDirectory(prefix='update-pr-validation-') as tmp:
        stage = pathlib.Path(tmp)
        for name in PUBLISH_FILES:
            shutil.copyfile((candidate if name in UPDATE_FILES else root) / name, stage / name)
        return snapshot(stage)


def run(root: pathlib.Path, *arguments: str) -> str:
    """Run checked Git/GitHub commands without interpolating artifact data into a shell."""
    return subprocess.check_output(arguments, cwd=root, text=True)


def main() -> None:
    """Publish one immutable candidate branch; never force-push or overwrite manual edits."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=pathlib.Path)
    args = parser.parse_args()
    root = pathlib.Path(__file__).resolve().parents[1]
    package = validate_candidate(root, args.candidate)
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid GitHub repository identity')
    if os.environ.get('GITHUB_REF') != 'refs/heads/main' or run(root, 'git', 'rev-parse', 'HEAD').strip() != os.environ.get('GITHUB_SHA'):
        raise ValueError('PR publication must use the trusted exact main commit')
    if not os.environ.get('GH_TOKEN'):
        raise ValueError('Configure UPSTREAM_PR_TOKEN so proposed PRs trigger validation workflows')
    branch = f"automation/upstream-{package['version']}-{package['hashes'][-1][:12]}"
    existing = json.loads(run(root, 'gh', 'pr', 'list', '--repo', repository, '--head', branch,
                             '--state', 'all', '--json', 'url,state'))
    if existing:
        print('Candidate already has a PR; preserve its current review state:', existing[0]['url'])
        return
    remote = f'https://github.com/{repository}.git'
    if run(root, 'git', '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
           'ls-remote', remote, 'refs/heads/' + branch).strip():
        raise ValueError('Candidate branch exists without a PR; inspect it manually before retrying')
    for name in UPDATE_FILES:
        shutil.copyfile(args.candidate / name, root / name)
    run(root, 'git', 'switch', '-c', branch)
    run(root, 'git', 'config', 'user.name', 'github-actions[bot]')
    run(root, 'git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    run(root, 'git', 'add', '--', *UPDATE_FILES)
    run(root, 'git', '-c', 'core.hooksPath=/dev/null', 'commit', '-m', f"chore: update Claude Desktop to {package['version']}")
    run(root, 'git', '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
        'push', remote, 'HEAD:refs/heads/' + branch)
    print(run(root, 'gh', 'pr', 'create', '--repo', repository, '--base', 'main', '--head', branch,
              '--title', f"Update Claude Desktop to {package['version']}",
              '--body-file', str(args.candidate / 'report.md')))


if __name__ == '__main__':
    main()
