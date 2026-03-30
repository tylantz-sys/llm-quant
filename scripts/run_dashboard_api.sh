#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/ty/Documents/llm-quant/llm-quant"
UVICORN_BIN="$REPO_DIR/.venv/bin/uvicorn"

if [[ ! -x "$UVICORN_BIN" ]]; then
  echo "uvicorn not found at $UVICORN_BIN" >&2
  echo "Install with: pip install -e \".[api]\"" >&2
  exit 1
fi

cd "$REPO_DIR"
"$UVICORN_BIN" llm_quant.dashboard.api:app --host 0.0.0.0 --port 8000
