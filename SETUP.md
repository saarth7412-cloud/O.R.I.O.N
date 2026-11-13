# O.R.I.O.N. v1 Setup

This guide sets up everything on an Apple Silicon Mac. Every Python command uses one shared environment, `~/.orion-venv`. Nothing else should be used for running O.R.I.O.N.

## 0. Required macOS permissions

Open System Settings → Privacy & Security and allow your terminal app (Terminal or iTerm) in each of these:

- **Microphone**: needed for voice input. macOS asks the first time; click Allow.
- **Screen Recording**: needed for `--screen` (the engine captures your screen). Quit and reopen the terminal after granting it.
- **Accessibility**: needed for AppleScript app control, such as "open calculator".

If something stops working later, come back here first.

## Retired environments

Older setups created separate environments in `~/orion/src/voice/.venv` and `~/orion/src/engine/.venv`. Both are retired. Everything runs from `~/.orion-venv`. If either old folder still exists, stop the engine and delete it after the shared environment works:

```bash
rm -rf ~/orion/src/voice/.venv ~/orion/src/engine/.venv
```

## 1. Create the shared environment (once)

```bash
python3.12 -m venv ~/.orion-venv
source ~/.orion-venv/bin/activate
pip install --upgrade pip
pip install fastapi "uvicorn[standard]" pydantic requests numpy sounddevice piper-tts
```

If `python3.12` isn't found, install it with `brew install python@3.12`.

Check that it works:

```bash
~/.orion-venv/bin/python -c "import fastapi, uvicorn, piper, sounddevice, requests, numpy; print('deps OK')"
```

## 2. Build the speech components (once)

Installs whisper.cpp with Metal, the `base.en` Whisper model, and the Piper voice. It is safe to re-run.

```bash
chmod +x ~/orion/src/voice/setup_voice_env.sh
~/orion/src/voice/setup_voice_env.sh
```

Then check speech input:

```bash
~/.orion-venv/bin/python ~/orion/src/voice/stt_wrapper.py --check
```

## 3. Install the local models (once)

The engine uses Ollama for conversational replies and screen questions.

```bash
brew install ollama
ollama serve            # leave this running in its own tab, or start the Ollama app
ollama pull llama3.2:3b
ollama pull qwen2.5vl:7b
ollama list
```

`ollama list` should show both models.

## 4. Start O.R.I.O.N. (every session)

Terminal tab 1, the engine. Leave it open:

```bash
~/orion/scripts/start_orion.sh
```

If it says the engine is already running, that's fine. You don't need a second copy.

Terminal tab 2, the voice loop:

```bash
~/.orion-venv/bin/python -u ~/orion/src/voice/client_bridge.py --loop
```

Say a command. Press `Ctrl + C` to stop the loop.

## 5. Verify v1

With the engine running:

```bash
# Conversational fallback. Expect a spoken answer and no HTTP 400.
~/.orion-venv/bin/python ~/orion/src/voice/client_bridge.py --text "hello"

# Screen question. Expect a spoken description of your screen.
~/.orion-venv/bin/python ~/orion/src/voice/client_bridge.py --text "what is on my screen" --screen

# Command path. Calculator should open.
~/.orion-venv/bin/python ~/orion/src/voice/client_bridge.py --text "open calculator"
```

## Troubleshooting

- **"Can't reach the engine"**: the engine isn't running. Start it with `~/orion/scripts/start_orion.sh`.
- **"Port 8765 is held by another process"**: run `lsof -nP -iTCP:8765 -sTCP:LISTEN`, then `kill <PID>`.
- **"SETUP REQUIRED"**: the message names the exact fix command. Run it.
- **Screen answers are empty or fail**: check that `ollama serve` is running and `ollama list` shows `qwen2.5vl:7b`, and that Screen Recording is allowed for your terminal.
- **Voice is silent or words are missing**: check System Settings → Sound → Input and make sure the built-in microphone is selected.
- **Git asks for a password**: use a GitHub personal access token, not your account password.

## Known limits in v1

- Turn-taking is half-duplex. The loop listens for a fixed clip length, then replies. It doesn't hear you while it speaks, so you can't interrupt it. Streaming voice activity detection is planned for v2.
- Whisper `base.en` sometimes mishears names and drops words.
