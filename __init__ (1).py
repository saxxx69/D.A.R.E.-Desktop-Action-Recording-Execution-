#!/usr/bin/env bash
# Wrapper for examples/synthetic_run.py — runs the full synthetic pipeline.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

PY=".venv/bin/python"
[ -x "$PY" ] || PY=".venv/Scripts/python"
[ -x "$PY" ] || PY="python3"

"$PY" examples/synthetic_run.py "$@"
