#!/usr/bin/env bash
# Propagate the actual lint process status instead of relying on devtools' tee.
set -euo pipefail
if (( $# != 2 )); then
  printf 'Usage: ci-namcap.sh CHROOT PACKAGE_STAGE\n' >&2
  exit 2
fi
shopt -s nullglob
packages=("$2"/*.pkg.tar.zst)
if (( ${#packages[@]} != 1 )); then
  printf 'Expected exactly one package for Namcap.\n' >&2
  exit 1
fi
arch-nspawn "$1" --bind="$2:/startdir" /usr/bin/sudo -u builduser \
  /usr/bin/namcap /startdir/PKGBUILD "/startdir/${packages[0]##*/}"
