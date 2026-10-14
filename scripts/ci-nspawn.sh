#!/usr/bin/env bash
# CI-only adapter: the Docker job has no system manager to allocate scope units.
# Devtools supplies --register=no; retain its namespace and isolation arguments.
set -euo pipefail
exec /usr/bin/systemd-nspawn --keep-unit "$@"
