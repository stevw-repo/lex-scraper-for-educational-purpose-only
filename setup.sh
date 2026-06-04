#!/usr/bin/env bash
#
# setup.sh — create the local virtualenv and install everything the scraper needs.
#
#   ./setup.sh
#
# Re-runnable: reuses an existing .venv and just (re)installs the dependencies
# and the Chromium build Playwright drives. Override the interpreter with
# PYTHON=/path/to/python3 ./setup.sh
#
set -euo pipefail

# Always operate from the project root (this script's directory).
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
VENV=".venv"

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "error: '$PY' not found. Install Python 3.10+ (or set PYTHON=/path/to/python3)." >&2
  exit 1
fi

echo "==> Using $("$PY" --version 2>&1) at $(command -v "$PY")"

if [ ! -d "$VENV" ]; then
  echo "==> Creating virtualenv in $VENV"
  "$PY" -m venv "$VENV"
else
  echo "==> Reusing existing virtualenv in $VENV"
fi

VPY="$VENV/bin/python"

echo "==> Upgrading pip"
"$VPY" -m pip install --quiet --upgrade pip

echo "==> Installing Python dependencies (requirements.txt)"
if ! "$VPY" -m pip install -r requirements.txt; then
  echo "==> pip failed; retrying with trusted hosts (TLS-intercepting network?)"
  "$VPY" -m pip install \
    --trusted-host pypi.org --trusted-host files.pythonhosted.org \
    -r requirements.txt
fi

echo "==> Installing the Playwright Chromium browser"
"$VPY" -m playwright install chromium

cat <<EOF

Done. Next:
  source $VENV/bin/activate        # then: python -m lex.cli serve
or run without activating:
  $VPY -m lex.cli serve            # web UI at http://127.0.0.1:8765
  $VPY -m lex.cli login            # CLI: log in once, then 'extract'
EOF
