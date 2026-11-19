#!/bin/sh
# Reproducible local setup. Downloads packages; never accounts, footage or model weights.
set -eu
RELAY_BOOTSTRAP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$RELAY_BOOTSTRAP_ROOT"
if command -v python3.12 >/dev/null 2>&1; then
  RELAY_BOOTSTRAP_PYTHON=python3.12
elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))'; then
  RELAY_BOOTSTRAP_PYTHON=python3
else
  echo "Install Python 3.12 from python.org or your platform package manager, then rerun ./bootstrap.sh."
  exit 1
fi
case "$(uname -s)" in
  Darwin|Linux) ;;
  *) echo "This release supports macOS and Linux. Windows needs a Linux host."; exit 1 ;;
esac
if [ ! -x .venv/bin/python ]; then
  "$RELAY_BOOTSTRAP_PYTHON" -m venv .venv
fi
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
if [ ! -f config.toml ]; then
  ./relay init
fi
echo "Setup complete. Start the app with ./relay start --open"
echo "Choose sources, install transcription and connect providers in Setup."
