#!/usr/bin/env bash
# start_engine.sh
# Project O.R.I.O.N. | starts the engine from the shared ~/.orion-venv.
# Safe to run twice: if the engine is already healthy, it says so and exits.
set -euo pipefail

PORT="${ORION_PORT:-8765}"
VENV="$HOME/.orion-venv"
ENGINE_DIR="$HOME/orion/src/engine"

if [[ ! -x "$VENV/bin/uvicorn" ]]; then
  echo "Missing $VENV/bin/uvicorn. Follow ~/orion/SETUP.md, section 1." >&2
  exit 1
fi

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  if curl -fs "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo "Engine is already running and healthy on port $PORT."
    exit 0
  fi
  echo "Port $PORT is held by another process that isn't the engine." >&2
  echo "Find it with: lsof -nP -iTCP:$PORT -sTCP:LISTEN" >&2
  exit 1
fi

cd "$ENGINE_DIR"
echo "Starting engine on http://127.0.0.1:$PORT (Ctrl+C to stop)"
exec "$VENV/bin/uvicorn" main:app --host 127.0.0.1 --port "$PORT"
