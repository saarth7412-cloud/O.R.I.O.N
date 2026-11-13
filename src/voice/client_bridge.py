#!/usr/bin/env python3
"""
client_bridge.py
Project O.R.I.O.N. | agent-voice
Connects speech-in (stt_wrapper) to the engine's POST /command and speech-out (tts_wrapper).
Reports live voice states to POST /state so the HUD can show them.

Mute toggle (loop mode): press the mute key (default Right Option), in any app, to mute or unmute the
microphone without stopping the script. Needs Input Monitoring permission on macOS.

Usage:
    python client_bridge.py --text "hello"              # skip the mic, test the engine path
    python client_bridge.py --seconds 5                 # one voice turn
    python client_bridge.py --loop                      # keep listening; Right Option toggles mute
    python client_bridge.py --loop --mute-key f8        # use a different mute key
    python client_bridge.py --loop --no-hotkey          # loop without the global key listener
    python client_bridge.py --loop --screen             # send screen context to the local VLM
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
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
HOTKEY_DEBOUNCE_S = 0.35      # ignore key auto-repeat and double presses
MUTE_POLL_S = 0.2             # how often the loop checks the mute flag while muted
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
# Mute control and global hotkey
# ---------------------------------------------------------------------------
class MicControl:
    """Thread-safe mute flag. The hotkey thread toggles it; the audio loop reads it."""

    def __init__(self, key_label: str = "Right Option") -> None:
        self.key_label = key_label
        self._muted = threading.Event()
        self._lock = threading.Lock()
        self._last_toggle = float("-inf")

    @property
    def muted(self) -> bool:
        return self._muted.is_set()

    def toggle(self) -> bool:
        """Flip the mute state. Returns False if the press was debounced."""
        now = time.monotonic()
        with self._lock:
            if now - self._last_toggle < HOTKEY_DEBOUNCE_S:
                return False
            self._last_toggle = now
            if self._muted.is_set():
                self._muted.clear()
                print("[mic] LISTENING. Speak when ready.", flush=True)
            else:
                self._muted.set()
                print(f"[mic] MUTED. Press {self.key_label} to resume.", flush=True)
                post_state("standby", "muted")
        return True


def start_hotkey_listener(control: MicControl, key_name: str = "alt_r"):
    """
    Start a global key listener for F8 and Right Ctrl. Returns the listener,
    or None if pynput can't be loaded (the loop still runs, without hotkeys).
    """
    try:
        from pynput import keyboard
    except Exception as exc:  # missing package or unsupported platform
        print(
            f"[mic] hotkeys disabled: could not load pynput ({exc}). Ctrl+C still works.",
            file=sys.stderr,
            flush=True,
        )
        return None

    if not hasattr(keyboard.Key, key_name):
        raise ValueError(
            f"Unknown mute key '{key_name}'. Try: alt_r, alt, cmd_r, shift_r, ctrl_r, caps_lock, f1 to f12."
        )
    toggle_keys = {getattr(keyboard.Key, key_name)}

    def on_press(key) -> None:
        if isinstance(key, keyboard.Key) and key in toggle_keys:
            control.toggle()

    listener = keyboard.Listener(on_press=on_press)
    listener.daemon = True
    listener.start()
    return listener


def pretty_key_label(name: str) -> str:
    """Human-readable key name for console messages."""
    labels = {
        "alt_r": "Right Option", "alt": "Option", "alt_l": "Left Option",
        "cmd_r": "Right Command", "cmd": "Command",
        "shift_r": "Right Shift", "ctrl_r": "Right Control", "caps_lock": "Caps Lock",
    }
    if re.fullmatch(r"f\d{1,2}", name):
        return name.upper()
    return labels.get(name, name.replace("_", " ").title())


def is_muted(control: MicControl | None) -> bool:
    return control is not None and control.muted


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
            "Fix: start it with ~/orion/scripts/start_orion.sh"
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
        print(f"You:   {prompt}", flush=True)
        reply = send_command(prompt, include_screen, url)
        print(f"Orion: {reply}", flush=True)
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


def run_loop(
    seconds: float,
    include_screen: bool,
    url: str,
    speak_aloud: bool,
    control: MicControl | None,
) -> None:
    """Keep listening. Muted time skips capture. Errors on one turn are printed and the loop continues."""
    print("Listening. Press Ctrl+C to stop.", flush=True)
    if control is not None:
        print(f"Mute toggle: {control.key_label}. Mute takes effect after the current clip ends.", flush=True)

    while True:
        if is_muted(control):
            time.sleep(MUTE_POLL_S)
            continue

        try:
            heard = listen_once(seconds)
        except STTRuntimeError as exc:
            print(f"[bridge] mic/transcription error: {exc}", file=sys.stderr, flush=True)
            continue

        if is_muted(control):
            # Muted while this clip was being captured: drop it.
            print("[mic] clip discarded (muted during capture).", flush=True)
            post_state("standby")
            continue

        if not heard:
            continue

        try:
            handle_turn(heard, include_screen, url, speak_aloud)
        except (BridgeError, TTSRuntimeError, ValueError) as exc:
            print(f"[bridge] ERROR: {exc}", file=sys.stderr, flush=True)


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
    parser.add_argument("--no-hotkey", action="store_true", help="Loop mode without the mute toggle.")
    parser.add_argument("--mute-key", default="alt_r", help="Key that toggles mute (default alt_r = Right Option).")
    parser.add_argument("--url", default=None, help="Override the engine URL.")
    args = parser.parse_args(argv)

    url = args.url or resolve_url()
    speak_aloud = not args.no_speak
    control: MicControl | None = None

    try:
        if args.text is not None:
            handle_turn(args.text, args.screen, url, speak_aloud)
        elif args.loop:
            if not args.no_hotkey:
                control = MicControl(pretty_key_label(args.mute_key))
                start_hotkey_listener(control, args.mute_key)
            run_loop(args.seconds, args.screen, url, speak_aloud, control)
        else:
            heard = listen_once(args.seconds)
            if not heard:
                print("Didn't catch anything. Try again and speak a little louder.")
                return 0
            handle_turn(heard, args.screen, url, speak_aloud)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
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
