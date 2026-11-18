#!/usr/bin/env bash
# Local launcher: creates the virtualenv on first run, then serves http://127.0.0.1:8770
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -e '.[dev]'
fi
exec .venv/bin/python -m cellarium serve --host "${CELLARIUM_HOST:-127.0.0.1}" --port "${CELLARIUM_PORT:-8770}" "$@"
