# O.R.I.O.N. v1

O.R.I.O.N. is a local assistant for macOS. The engine is a FastAPI server that routes commands to apps, Shortcuts, a text model, and a vision model. The voice client and the native HUD talk to it over HTTP and WebSocket.

## Layout

```
orion/
├── requirements.txt          dependencies for the engine and voice client
├── scripts/
│   └── start_orion.sh        frees port 8765 and starts the engine
├── tests/
│   └── verify_v1.py          checks every v1 item against the live engine
└── src/
    ├── engine/               FastAPI engine (main.py, text_core.py, vision_core.py, ...)
    ├── voice/                voice client (client_bridge.py)
    └── native-hud/           macOS HUD
```

## Environment

There is one shared environment for the whole project: `~/.orion-venv`, built with Python 3.12. Each project folder used to have its own `.venv`. Those are retired, so you can delete `src/engine/.venv` and `src/voice/.venv` if they still exist.

Create the environment once:

```bash
python3.12 -m venv ~/.orion-venv
source ~/.orion-venv/bin/activate
pip install -r ~/orion/requirements.txt
```

Some packages need system tools. Install them once with Homebrew:

```bash
brew install portaudio ffmpeg
```

`portaudio` is needed by `sounddevice`, and `ffmpeg` is needed by Whisper.

Ollama must be running with these models available:

```bash
ollama list
```

You should see `qwen2-vl`, which is the name the engine uses for the vision model, and `llama3.2:3b` for chat.

## Start the engine

Always start the engine with the launcher:

```bash
~/orion/scripts/start_orion.sh
```

The launcher stops anything already listening on port 8765, activates `~/.orion-venv`, and starts uvicorn on `127.0.0.1`. Leave that window open while you use O.R.I.O.N.

To use a different port, set `ORION_PORT` first:

```bash
ORION_PORT=8800 ~/orion/scripts/start_orion.sh
```

## Verify

With the engine running, open a second tab and run:

```bash
cd ~/orion
source ~/.orion-venv/bin/activate
python tests/verify_v1.py
```

The core checks should report `10 passed, 0 failed, 1 skipped`. Add `--screen` to also check the live screen question, which needs Ollama running and Screen Recording permission for your terminal. The full run should report `11 passed, 0 failed, 0 skipped`.

## Engine endpoints

**`GET /health`** returns the server status, the number of connected HUD clients, and the port.

**`POST /command`** takes one command:

```json
{"source": "voice", "prompt": "What is the capital of Japan?", "include_screen": false}
```

It returns:

```json
{"command_id": "...", "status": "COMPLETED", "reply": "...", "action": "chat", "output": "...", "error": null}
```

`reply` and `output` contain the same text. `action` is one of `builtin`, `open_app`, `shortcut`, `screen`, `chat`, or `noop`.

A blank prompt, such as an empty or whitespace-only voice transcript, returns HTTP 200 with `action` set to `noop` and an empty `reply`. Silence and muted turns never produce an error. The voice client also normally skips sending silent turns, but the engine handles them safely if they arrive.

Commands are routed in this order:

- `time`, `date`, `help` are answered instantly.
- `open <App Name>` opens an app.
- `shortcut <Name> | <input>` runs a Shortcut. The `| <input>` part is optional.
- Any request with `include_screen: true` answers a question about the screen.
- Everything else is a chat question for the text model.

Errors: a bare `open` or `shortcut` returns HTTP 400. If Ollama isn't running, the server returns HTTP 503 with `{"reply": "Local language model is offline. Please start Ollama.", ...}`.

**`POST /state`** lets the voice client push HUD states:

```json
{"state": "listening", "text": "", "source": "voice"}
```

`state` is required. `text` defaults to an empty string, and `source` defaults to `"voice"`. The engine broadcasts it to every HUD client as:

```json
{"type": "state", "state": "listening", "text": "", "source": "voice", "ts": "..."}
```

The voice client uses `listening` when recording starts, `transcribing` during speech-to-text, and `standby` when the turn ends.

**`WS /events`** streams HUD events. The first message is a hello with `"state": "standby"`. During a command the engine sends `thinking`, then `executing` for apps and Shortcuts, and finishes with `standby`. The engine never sends `idle`.

## HUD states

| State | Sent by | Meaning |
| --- | --- | --- |
| `standby` | engine and voice client | resting state |
| `listening` | voice client, via `/state` | recording the microphone |
| `transcribing` | voice client, via `/state` | converting speech to text |
| `thinking` | engine | a command has started |
| `executing` | engine | an app or Shortcut is running |
| `online` | HUD | available for voice input |

## Voice client hotkeys

The voice client (`src/voice/client_bridge.py`) can use global push-to-talk and mute hotkeys, built with `pynput`. These run entirely in the voice client. The engine doesn't know about them and needs no changes.

macOS needs two permissions for global hotkeys. Grant both to the app that runs the voice client, such as iTerm, under System Settings → Privacy & Security:

- **Accessibility**
- **Input Monitoring**

Quit and reopen that app after granting them. Without these permissions, the hotkeys do nothing and no error appears in the engine.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `ORION_PORT` | `8765` | engine port |
| `ORION_VISION_MAX_DIM` | `768` | longest screenshot edge in pixels sent to the vision model; lower is faster |
| `ORION_VENV` | `~/.orion-venv` | environment used by the launcher |

## Git

Commit changes from the project root:

```bash
cd ~/orion
git status
git add <files you changed>
git commit -m "<short description>"
git push -u origin main
```

`.gitignore` keeps virtual environments, caches, and `.env` files out of Git.
