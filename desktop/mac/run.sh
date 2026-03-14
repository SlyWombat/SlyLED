#!/usr/bin/env bash
# SlyLED Mac Parent — launcher
set -euo pipefail

PORT=${1:-5000}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Prefer python3
PYTHON=$(command -v python3 || command -v python)
if [ -z "$PYTHON" ]; then
    echo "Error: Python 3.10+ required but not found." >&2
    exit 1
fi

# Install/upgrade dependencies quietly if needed
"$PYTHON" -m pip install -q -r "$SCRIPT_DIR/requirements.txt"

echo "Starting SlyLED parent on http://localhost:$PORT ..."
"$PYTHON" "$ROOT/desktop/shared/parent_server.py" --port "$PORT"
