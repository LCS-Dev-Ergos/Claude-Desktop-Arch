#!/usr/bin/bash
# Launch the packaged application with sandboxing and a restricted option set.
set -euo pipefail

# Reject unrestricted Chromium switches while allowing useful display options.
validate_flag() {
  case "$1" in
    --ozone-platform=wayland|--ozone-platform=x11|--ozone-platform=auto|\
    --ozone-platform-hint=auto|--ozone-platform-hint=wayland|--ozone-platform-hint=x11|\
    --disable-gpu|--disable-gpu-compositing|--force-device-scale-factor=[0-9]*|\
    --password-store=gnome-libsecret|--password-store=kwallet5|--password-store=kwallet6|\
    --enable-sandbox|--startup) ;;
    *) printf 'Unsupported option: %s\n' "$1" >&2; exit 2 ;;
  esac
}

flags=()
config="${XDG_CONFIG_HOME:-$HOME/.config}/claude-desktop-flags.conf"
if [[ -r $config ]]; then
  while IFS= read -r line || [[ -n $line ]]; do
    line="${line%%#*}"
    # One argument per line; trim surrounding whitespace without evaluating it.
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -n $line ]] || continue
    validate_flag "$line"
    flags+=("$line")
  done < "$config"
fi
for argument in "$@"; do
  if [[ $argument == -* ]]; then
    validate_flag "$argument"
  fi
done

if (( EUID == 0 )); then
  printf 'Claude Desktop must run as an ordinary user.\n' >&2
  exit 1
fi
runtime=/usr/lib/claude-desktop/claude-desktop
if [[ ! -x $runtime ]]; then
  printf 'Packaged runtime is missing: %s\n' "$runtime" >&2
  exit 1
fi
platform=()
if [[ -n ${WAYLAND_DISPLAY:-} || ${XDG_SESSION_TYPE:-} == wayland ]]; then
  platform=(--ozone-platform=wayland)
  for argument in "${flags[@]}" "$@"; do
    case "$argument" in --ozone-platform=*|--ozone-platform-hint=*) platform=() ;; esac
  done
fi
# Prevent Node/Electron environment hooks from changing the application entry point.
unset ELECTRON_RUN_AS_NODE ELECTRON_NO_SANDBOX NODE_OPTIONS NODE_PATH
exec "$runtime" --enable-sandbox "${platform[@]}" "${flags[@]}" "$@"
