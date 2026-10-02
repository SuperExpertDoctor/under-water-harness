#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
cd "$root"

if [ -x tools/.venv/bin/python ]; then
    exec tools/.venv/bin/python adapter/launcher.py "$@"
fi
exec python3 adapter/launcher.py "$@"
