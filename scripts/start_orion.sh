#!/usr/bin/env bash
# O.R.I.O.N. launcher: frees the engine port, then starts the FastAPI engine.
#
# Usage:  scripts/start_orion.sh
# Optional overrides:
#   ORION_PORT=8765          port to bind (default 8765)
#   ORION_VENV=~/.orion-venv Python environment to use

set -euo pipefail

PORT="${ORION_PORT:-8765}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE_DIR="$SCRIPT_DIR/../src/engine"
VENV="${ORION_VENV:-$HOME/.orion-venv}"

if [ ! -d "$ENGINE_DIR" ]; then
  echo "Engine folder not found at: $ENGINE_DIR" >&2
  exit 1
fi

# Fall back to the engine's own venv if the shared one hasn't been created yet.
if [ ! -f "$VENV/bin/activate" ]; then
  if [ -f "$ENGINE_DIR/.venv/bin/activate" ]; then
    echo "Note: $VENV not found, using $ENGINE_DIR/.venv instead."
    VENV="$ENGINE_DIR/.venv"
  else
    echo "No virtual environment found. Create one with:" >&2
    echo "  python3.12 -m venv ~/.orion-venv" >&2
    echo "  source ~/.orion-venv/bin/activate" >&2
    echo "  pip install -r ~/orion/requirements.txt" >&2
    exit 1
  fi
fi

# Stop anything already listening on the port so the new engine can bind.
STALE_PIDS="$(lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$STALE_PIDS" ]; then
  echo "Stopping stale process(es) on port $PORT: $STALE_PIDS"
  # shellcheck disable=SC2086
  kill $STALE_PIDS 2>/dev/null || true
  sleep 1
  # Force-stop anything still holding the port after a graceful kill.
  STILL_PIDS="$(lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$STILL_PIDS" ]; then
    # shellcheck disable=SC2086
    kill -9 $STILL_PIDS 2>/dev/null || true
  fi
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"
cd "$ENGINE_DIR"

echo "Starting O.R.I.O.N. engine on http://127.0.0.1:$PORT (Ctrl+C to stop)"
exec uvicorn main:app --host 127.0.0.1 --port "$PORT"
