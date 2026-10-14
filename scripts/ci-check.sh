#!/usr/bin/env bash
# Run source validation as an ordinary user without publication credentials.
set -euo pipefail
mkdir -p .ci-output
python scripts/ci_policy.py snapshot . > .ci-output/snapshot.json
python -m unittest discover -s tests -v 2>&1 | tee .ci-output/tests.log
bash -n PKGBUILD claude-desktop.sh scripts/ci-*.sh
shellcheck claude-desktop.sh scripts/ci-*.sh
actionlint .github/workflows/*.yml
makepkg --printsrcinfo > .ci-output/generated.SRCINFO
diff -u .SRCINFO .ci-output/generated.SRCINFO
version=$(python -c 'import json; print(json.load(open(".ci-output/snapshot.json"))["version"])')
set +e
python scripts/check-update.py --version "$version" 2>&1 | tee .ci-output/upstream-authentication.log
status=${PIPESTATUS[0]}
set -e
if [[ $status != 3 ]]; then
  printf 'Pinned release authentication failed (expected updater exit 3, got %s).\n' "$status" >&2
  exit 1
fi
makepkg -f --verifysource 2>&1 | tee .ci-output/source-integrity.log
