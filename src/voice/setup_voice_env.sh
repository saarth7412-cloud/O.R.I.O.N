#!/usr/bin/env bash
# setup_voice_env.sh
# Project O.R.I.O.N. | agent-voice workspace bootstrap
# Target: Apple Silicon macOS. Idempotent: safe to re-run.
set -euo pipefail

ORION_ROOT="${ORION_ROOT:-$HOME/orion}"
VOICE_DIR="$ORION_ROOT/src/voice"
VENDOR_DIR="$ORION_ROOT/vendor"
MODEL_DIR="$ORION_ROOT/models"
VENV_DIR="${ORION_VENV:-$HOME/.orion-venv}"
WHISPER_DIR="$VENDOR_DIR/whisper.cpp"
WHISPER_MODEL="base.en"
WHISPER_MODEL_FILE="$MODEL_DIR/ggml-$WHISPER_MODEL.bin"
PIPER_VOICE="en_US-lessac-medium"
PIPER_BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
ORCHESTRATOR_URL="http://127.0.0.1:8765/command"

log() { printf '\033[1;36m[orion-setup]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[orion-setup] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# 1. Platform guard
[[ "$(uname -s)" == "Darwin" ]] || die "This workspace targets macOS only."
[[ "$(uname -m)" == "arm64" ]] || die "Apple Silicon (arm64) required for Metal acceleration."

# 2. Toolchain
command -v brew >/dev/null 2>&1 || die "Homebrew missing. Install from https://brew.sh, then re-run."
command -v cmake >/dev/null 2>&1 || brew install cmake
command -v git >/dev/null 2>&1 || die "git missing. Run: xcode-select --install"
command -v python3 >/dev/null 2>&1 || die "python3 missing. Run: brew install python@3.12"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python 3.10+ required."

# 3. Directory layout
mkdir -p "$VOICE_DIR" "$VENDOR_DIR" "$MODEL_DIR" "$MODEL_DIR/piper"
log "Workspace ready at $ORION_ROOT"

# 4. whisper.cpp with Metal acceleration
if [[ ! -d "$WHISPER_DIR/.git" ]]; then
  log "Cloning whisper.cpp..."
  git clone --depth 1 https://github.com/ggerganov/whisper.cpp.git "$WHISPER_DIR"
fi
log "Building whisper.cpp with Metal..."
cmake -S "$WHISPER_DIR" -B "$WHISPER_DIR/build" \
  -DCMAKE_BUILD_TYPE=Release -DGGML_METAL=ON
cmake --build "$WHISPER_DIR/build" --config Release -j"$(sysctl -n hw.ncpu)"

# 5. Whisper model
if [[ ! -f "$WHISPER_MODEL_FILE" ]]; then
  log "Downloading Whisper model $WHISPER_MODEL (~150 MB)..."
  (cd "$WHISPER_DIR" && bash models/download-ggml-model.sh "$WHISPER_MODEL")
  cp "$WHISPER_DIR/models/ggml-$WHISPER_MODEL.bin" "$WHISPER_MODEL_FILE"
fi

# 6. Piper voice
for ext in onnx onnx.json; do
  target="$MODEL_DIR/piper/$PIPER_VOICE.$ext"
  if [[ ! -f "$target" ]]; then
    log "Downloading Piper voice file: $PIPER_VOICE.$ext"
    curl -fL --retry 3 -o "$target" "$PIPER_BASE_URL/$PIPER_VOICE.$ext"
  fi
done

# 7. Python virtual environment
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  log "Creating virtualenv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV_DIR/bin/python" -m pip install --upgrade -r "$VOICE_DIR/requirements.txt"

# 8. Emit environment contract for downstream modules
cat > "$VOICE_DIR/voice.env" <<ENVEOF
ORION_WHISPER_CLI=$WHISPER_DIR/build/bin/whisper-cli
ORION_WHISPER_MODEL=$WHISPER_MODEL_FILE
ORION_PIPER_MODEL=$MODEL_DIR/piper/$PIPER_VOICE.onnx
ORION_ORCHESTRATOR_URL=$ORCHESTRATOR_URL
ENVEOF
log "Wrote $VOICE_DIR/voice.env"

# 9. Verification
"$WHISPER_DIR/build/bin/whisper-cli" --help >/dev/null 2>&1 \
  && log "whisper-cli: OK" || die "whisper-cli failed to run."
"$VENV_DIR/bin/python" -c "import piper, sounddevice, numpy, requests, pynput; print('python deps: OK')" \
  || die "Python dependency import failed."

log "Voice workspace bootstrap complete."
