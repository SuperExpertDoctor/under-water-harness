#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
cd "$root"

if [ -x tools/.venv/bin/python ]; then
    exec tools/.venv/bin/python tools/launcher.py "$@"
fi
exec python3 tools/launcher.py "$@"
