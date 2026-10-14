#!/usr/bin/env bash
# Prepare ordinary upstream updates, preserving the manual compatibility gate.
set -euo pipefail
mkdir -p .ci-output
set +e
python scripts/check-update.py --apply > .ci-output/upstream.log 2>&1
status=$?
set -e
cat .ci-output/upstream.log
python - "$status" <<'PY'
import os
import pathlib
import shutil
import sys

sys.path.insert(0, 'scripts')
from ci_policy import snapshot, update_status

state = update_status(int(sys.argv[1]))
log = pathlib.Path('.ci-output/upstream.log').read_text()
report = '# Authenticated upstream check\n\nStatus: `' + state + '`\n\n```diff\n' + log + '\n```\n'
if state == 'review_required':
    report += '\nPrepare and review this compatibility change manually; automation never uses --reviewed.\n'
if len(report.encode()) > 256 * 1024:
    raise ValueError('Update report exceeds the artifact limit')
pathlib.Path('.ci-output/report.md').write_text(report)
if state == 'candidate':
    snapshot(pathlib.Path.cwd())
    target = pathlib.Path('.ci-output/update')
    target.mkdir()
    for name in ('PKGBUILD', '.SRCINFO', 'upstream.json'):
        shutil.copyfile(name, target / name)
    (target / 'report.md').write_text(report)
if os.environ.get('GITHUB_OUTPUT'):
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('status=' + state + '\n')
if os.environ.get('GITHUB_STEP_SUMMARY'):
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
        summary.write(report)
PY
