#!/usr/bin/env bash
# Build a validated source snapshot in a fresh devtools working copy.
set -euo pipefail
if (( EUID != 0 )); then
  printf 'ci-build.sh requires root for devtools; the package build itself is unprivileged.\n' >&2
  exit 1
fi
root=$(pwd -P)
build_user=${CI_BUILD_USER:-builder}
if [[ $(id -u "$build_user") == 0 ]]; then
  printf 'The build user must be unprivileged.\n' >&2
  exit 1
fi
: "${RUNNER_TEMP:?Set RUNNER_TEMP to a dedicated writable validation directory}"
chroot=${CI_CHROOT_DIR:-$RUNNER_TEMP/arch-chroot}
mkdir -p "$chroot" "$root/.ci-output"
stage=$(mktemp -d "$RUNNER_TEMP/claude-build.XXXXXX")
python scripts/ci_policy.py snapshot . > "$root/.ci-output/build-snapshot.json"
for name in PKGBUILD .SRCINFO claude-desktop.sh upstream.py package-assets.py upstream.json LICENSE.md; do
  cp -- "$root/$name" "$stage/$name"
done
version=$(python -c 'import json; print(json.load(open(".ci-output/build-snapshot.json"))["version"])')
asset="claude-desktop_${version}_amd64.deb"
if [[ -f $root/.cache/$asset ]]; then
  cp --reflink=auto -- "$root/.cache/$asset" "$stage/$asset"
fi
chown -R "$build_user:" "$stage"
if [[ ! -d $chroot/root ]]; then
  mkarchroot -C "$root/scripts/ci-pacman.conf" -M /usr/share/devtools/makepkg.conf.d/x86_64.conf "$chroot/root" base-devel
fi
(
  cd "$stage"
  SUDO_USER=$build_user makechrootpkg -c -u -n -r "$chroot" -l ci -U "$build_user" -- -f
) 2>&1 | tee "$root/.ci-output/chroot-build.log"
arch-nspawn "$chroot/ci" --bind="$stage:/startdir" /usr/bin/sudo -u builduser \
  /usr/bin/bash -c 'cd /startdir && makepkg -f --verifysource' 2>&1 | tee "$root/.ci-output/chroot-integrity.log"
arch-nspawn "$chroot/ci" /usr/bin/pacman -Qkk claude-desktop-sandboxed 2>&1 | tee "$root/.ci-output/installed-integrity.log"
bash scripts/ci-namcap.sh "$chroot/ci" "$stage" 2>&1 | tee "$root/.ci-output/namcap.log"
python scripts/ci_policy.py namcap .ci-output/namcap.log
python - "$stage" "$root" <<'PY'
import hashlib
import pathlib
import sys
import tarfile

stage, root = map(pathlib.Path, sys.argv[1:])
artifacts = list(stage.glob('*.pkg.tar.zst'))
assert len(artifacts) == 1, 'Expected one package artifact'
with tarfile.open(artifacts[0]) as archive:
    entries = archive.getmembers()
    assert all(item.uid == 0 and item.gid == 0 and not item.mode & 0o6000 for item in entries)
    assert not any(item.name == '.INSTALL' or item.name.endswith('/chrome-sandbox') for item in entries)
    assert archive.extractfile('usr/share/licenses/claude-desktop-sandboxed/Packaging-MIT').read() == (root / 'LICENSE.md').read_bytes()
    assert archive.extractfile('usr/bin/claude-desktop').read() == (root / 'claude-desktop.sh').read_bytes()
    (root / '.ci-output/BUILDINFO').write_bytes(archive.extractfile('.BUILDINFO').read())
with artifacts[0].open('rb') as source:
    digest = hashlib.file_digest(source, 'sha256').hexdigest()
(root / '.ci-output/package.sha256').write_text(digest + '  ' + artifacts[0].name + '\n')
print('Archive ownership, privilege bits, launcher, and packaging license passed.')
PY
