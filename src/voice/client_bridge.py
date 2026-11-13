#!/usr/bin/env python3
"""
client_bridge.py
Project O.R.I.O.N. | agent-voice
Connects speech-in (stt_wrapper) to the engine's POST /command and speech-out (tts_wrapper).
Reports live voice states to POST /state so the HUD can show them.

Usage:
    python client_bridge.py --text "hello"              # skip the mic, test the engine path
    python client_bridge.py --seconds 5                 # one voice turn
    python client_bridge.py --loop                      # keep listening until Ctrl+C
    python client_bridge.py --loop --screen             # send screen context to the local VLM
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import threading
from pathlib import Path

import requests

VOICE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(VOICE_DIR))

from stt_wrapper import (  # noqa: E402
    DEFAULT_SECONDS,
    ENV_FILE,
    STTRuntimeError,
    STTSetupError,
    _read_env_file,
    listen_and_transcribe,
)
from tts_wrapper import (  # noqa: E402
    MAX_CHARS,
    TTSRuntimeError,
    TTSSetupError,
    speak,
)

DEFAULT_URL = "http://127.0.0.1:8765/command"
SOURCE_NAME = "voice"
REQUEST_TIMEOUT_S = 120       # local VLM calls can be slow
STATE_TIMEOUT_S = 0.5         # state updates must never hold up the audio loop
ORION_PORT = os.environ.get("ORION_PORT") or "8765"
STATE_URL = f"http://127.0.0.1:{ORION_PORT}/state"


class BridgeError(RuntimeError):
    """Raised when the engine cannot be reached or returns a failure."""


# ---------------------------------------------------------------------------
# HUD state reporting
# ---------------------------------------------------------------------------
def _send_state(state: str, text: str) -> None:
    try:
        requests.post(
            STATE_URL,
            json={"state": state, "text": text, "source": SOURCE_NAME},
            timeout=STATE_TIMEOUT_S,
        )
    except Exception:  # a HUD hiccup must never stall or crash the audio loop
        pass


def post_state(state: str, text: str = "") -> None:
    """Fire-and-forget state update on a daemon thread."""
    threading.Thread(target=_send_state, args=(state, text), daemon=True).start()


# ---------------------------------------------------------------------------
# Engine calls
# ---------------------------------------------------------------------------
def resolve_url() -> str:
    """Process environment overrides voice.env, which overrides the default."""
    return (
        os.environ.get("ORION_ORCHESTRATOR_URL")
        or _read_env_file(ENV_FILE).get("ORION_ORCHESTRATOR_URL")
        or DEFAULT_URL
    )


def clean_prompt(text: str) -> str:
    """Remove stray trailing punctuation and extra spaces that Whisper adds to short phrases."""
    return re.sub(r"\s+", " ", text).strip().rstrip(".,!?;:").strip()


def send_command(prompt: str, include_screen: bool, url: str) -> str:
    """POST one command to the engine and return its output text."""
    payload = {
        "source": SOURCE_NAME,
        "prompt": clean_prompt(prompt),
        "include_screen": include_screen,
    }
    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_S)
    except requests.ConnectionError as exc:
        raise BridgeError(
            f"Can't reach the engine at {url}.\n"
            "Fix: start it with ~/orion/start_engine.sh"
        ) from exc
    except requests.Timeout as exc:
        raise BridgeError(f"The engine took longer than {REQUEST_TIMEOUT_S}s to respond.") from exc

    if response.status_code != 200:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise BridgeError(f"Engine returned HTTP {response.status_code}: {detail}")

    data = response.json()
    if data.get("status") != "COMPLETED":
        raise BridgeError(data.get("error") or "The engine reported a failed command.")
    return (data.get("output") or "").strip()


# ---------------------------------------------------------------------------
# Speech output
# ---------------------------------------------------------------------------
def chunk_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Split a long reply into speakable pieces, breaking at sentence ends where possible."""
    pieces: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        while len(sentence) > limit:
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        if sentence:
            pieces.append(sentence)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current} {piece}".strip()
        if len(candidate) > limit and current:
            chunks.append(current)
            current = piece
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# Turns and listening
# ---------------------------------------------------------------------------
def handle_turn(prompt: str, include_screen: bool, url: str, speak_aloud: bool) -> None:
    """Send one prompt, print the reply, speak it, then report standby when done."""
    try:
        print(f"You:   {prompt}")
        reply = send_command(prompt, include_screen, url)
        print(f"Orion: {reply}")
        if speak_aloud and reply:
            for chunk in chunk_text(reply):
                speak(chunk)
    finally:
        post_state("standby")


def listen_once(seconds: float) -> str:
    """Record one clip and return the transcript. Reports listening/transcribing/standby."""
    try:
        heard = listen_and_transcribe(seconds, on_stage=post_state)
    except Exception:
        post_state("standby")
        raise
    if not heard:
        post_state("standby")  # silence or nothing recognized
    return heard


def run_loop(seconds: float, include_screen: bool, url: str, speak_aloud: bool) -> None:
    """Keep listening. Errors on one turn are printed and the loop continues."""
    print("Listening. Press Ctrl+C to stop.")
    while True:
        try:
            heard = listen_once(seconds)
        except STTRuntimeError as exc:
            print(f"[bridge] mic/transcription error: {exc}", file=sys.stderr)
            continue
        if not heard:
            continue
        try:
            handle_turn(heard, include_screen, url, speak_aloud)
        except (BridgeError, TTSRuntimeError, ValueError) as exc:
            print(f"[bridge] ERROR: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="O.R.I.O.N. voice bridge to the engine.")
    parser.add_argument("--text", help="Send this text instead of listening to the mic.")
    parser.add_argument(
        "--seconds", type=float, default=DEFAULT_SECONDS,
        help=f"Length of each mic clip in seconds (default {DEFAULT_SECONDS}).",
    )
    parser.add_argument("--loop", action="store_true", help="Keep listening until Ctrl+C.")
    parser.add_argument("--screen", action="store_true", help="Include screen context (needs Ollama).")
    parser.add_argument("--no-speak", action="store_true", help="Print replies without speaking them.")
    parser.add_argument("--url", default=None, help="Override the engine URL.")
    args = parser.parse_args(argv)

    url = args.url or resolve_url()
    speak_aloud = not args.no_speak

    try:
        if args.text is not None:
            handle_turn(args.text, args.screen, url, speak_aloud)
        elif args.loop:
            run_loop(args.seconds, args.screen, url, speak_aloud)
        else:
            heard = listen_once(args.seconds)
            if not heard:
                print("Didn't catch anything. Try again and speak a little louder.")
                return 0
            handle_turn(heard, args.screen, url, speak_aloud)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except STTSetupError as exc:
        print(f"[bridge] SETUP REQUIRED (speech input): {exc}", file=sys.stderr)
        return 2
    except TTSSetupError as exc:
        print(f"[bridge] SETUP REQUIRED (speech output): {exc}", file=sys.stderr)
        return 2
    except (BridgeError, STTRuntimeError, TTSRuntimeError, ValueError) as exc:
        print(f"[bridge] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
