#!/bin/zsh
# Internal: the background job started by run_bg.sh (first-run install, then the scrape with the given arguments).
cd "$(dirname "$0")/.." || exit 1
if [ ! -x .venv/bin/python ]; then
  PY=""
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v $c >/dev/null 2>&1 && $c -c 'import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,13) else 1)'; then PY=$c; break; fi
  done
  [ -n "$PY" ] || { echo "ERROR: Python 3.11-3.13 is needed"; echo "EXIT=1"; exit 1; }
  echo "Installing (first run, 2-3 min)..."
  $PY -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt || { echo "ERROR: install failed"; echo "EXIT=1"; exit 1; }
fi
PYTHONUNBUFFERED=1 .venv/bin/python -m fbreviews "$@"
echo "EXIT=$?"
