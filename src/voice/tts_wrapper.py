#!/usr/bin/env python3
"""
tts_wrapper.py
Project O.R.I.O.N. | agent-voice
Turns text into speech locally with Piper, then plays it with macOS afplay.

Usage:
    python tts_wrapper.py --check
    python tts_wrapper.py --text "Good morning, Saarth."
    python tts_wrapper.py --text "Saved reply" --out reply.wav --no-play
    echo "Piped text" | python tts_wrapper.py
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ORION_ROOT = Path(os.environ.get("ORION_ROOT", Path.home() / "orion"))
VOICE_DIR = Path(__file__).resolve().parent
ENV_FILE = VOICE_DIR / "voice.env"
DEFAULT_PIPER_MODEL = ORION_ROOT / "models" / "piper" / "en_US-lessac-medium.onnx"

MAX_CHARS = 2000            # one synthesis call; split longer text into chunks
SYNTH_TIMEOUT_S = 60
PLAY_TIMEOUT_S = 300
MIN_WAV_BYTES = 44          # a WAV header with no audio data


class TTSSetupError(RuntimeError):
    """Raised when a dependency is missing. Message contains the fix."""


class TTSRuntimeError(RuntimeError):
    """Raised when synthesis or playback fails at runtime."""


@dataclass(frozen=True)
class TTSConfig:
    piper_model: Path


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_config() -> TTSConfig:
    """Process environment overrides voice.env, which overrides built-in defaults."""
    file_values = _read_env_file(ENV_FILE)
    raw = (
        os.environ.get("ORION_PIPER_MODEL")
        or file_values.get("ORION_PIPER_MODEL")
        or str(DEFAULT_PIPER_MODEL)
    )
    return TTSConfig(piper_model=Path(raw).expanduser())


def clean_text(raw: str) -> str:
    """Collapse whitespace and newlines so Piper reads one clean block of text."""
    return re.sub(r"\s+", " ", raw).strip()


def ensure_ready(config: TTSConfig) -> None:
    """Validate every dependency before synthesis. Raises TTSSetupError with the fix."""
    if sys.platform != "darwin":
        raise TTSSetupError("TTS workspace targets macOS only (playback uses afplay).")

    if importlib.util.find_spec("piper") is None:
        raise TTSSetupError(
            "Python package 'piper-tts' is missing.\n"
            f"Fix: {sys.executable} -m pip install piper-tts"
        )

    if not config.piper_model.is_file():
        raise TTSSetupError(
            f"Piper voice model not found at: {config.piper_model}\n"
            f"Fix: bash {VOICE_DIR / 'setup_voice_env.sh'}"
        )

    config_file = Path(str(config.piper_model) + ".json")
    if not config_file.is_file():
        raise TTSSetupError(
            f"Piper voice config not found at: {config_file}\n"
            f"Fix: bash {VOICE_DIR / 'setup_voice_env.sh'}"
        )

    if shutil.which("afplay") is None:
        raise TTSSetupError("afplay is missing. It ships with macOS; check your system install.")


def synthesize_to_file(text: str, out_path: Path, config: TTSConfig | None = None) -> Path:
    """Render text to a WAV file with Piper. Returns the path that was written."""
    config = config or load_config()
    ensure_ready(config)

    cleaned = clean_text(text)
    if not cleaned:
        raise ValueError("There is no text to speak.")
    if len(cleaned) > MAX_CHARS:
        raise ValueError(
            f"Text is {len(cleaned)} characters; the limit per call is {MAX_CHARS}. "
            "Split it into smaller pieces."
        )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable, "-m", "piper",
        "--model", str(config.piper_model),
        "--output_file", str(out_path),
    ]
    try:
        result = subprocess.run(
            command,
            input=cleaned,
            capture_output=True,
            text=True,
            timeout=SYNTH_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TTSRuntimeError(f"Piper exceeded {SYNTH_TIMEOUT_S}s while synthesizing.") from exc

    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-5:]
        raise TTSRuntimeError(
            f"Piper exited with code {result.returncode}.\n" + "\n".join(tail)
        )

    if not out_path.is_file() or out_path.stat().st_size < MIN_WAV_BYTES:
        raise TTSRuntimeError("Piper finished but produced no audio.")

    return out_path


def play_file(path: Path) -> None:
    """Play an audio file through the default macOS output device."""
    try:
        subprocess.run(["afplay", str(path)], check=True, timeout=PLAY_TIMEOUT_S)
    except subprocess.CalledProcessError as exc:
        raise TTSRuntimeError(f"Playback failed for {path.name} (afplay code {exc.returncode}).") from exc
    except subprocess.TimeoutExpired as exc:
        raise TTSRuntimeError(f"Playback of {path.name} exceeded {PLAY_TIMEOUT_S}s.") from exc


def speak(text: str, out_path: Path | None = None, play: bool = True) -> Path | None:
    """
    Speak text aloud, save it to out_path, or both.
    Returns out_path when a file was requested, otherwise None.
    """
    if out_path is None and not play:
        raise ValueError("Nothing to do: give an output path or allow playback.")

    config = load_config()
    ensure_ready(config)

    if out_path is not None:
        saved = synthesize_to_file(text, out_path, config)
        if play:
            play_file(saved)
        return saved

    fd, tmp_name = tempfile.mkstemp(prefix="orion_tts_", suffix=".wav")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        synthesize_to_file(text, tmp_path, config)
        play_file(tmp_path)
        return None
    finally:
        tmp_path.unlink(missing_ok=True)


def run_check() -> None:
    """Confirm setup, then synthesize a short phrase to prove Piper actually works."""
    config = load_config()
    ensure_ready(config)

    fd, tmp_name = tempfile.mkstemp(prefix="orion_tts_check_", suffix=".wav")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        synthesize_to_file("Orion voice check complete.", tmp_path, config)
        size = tmp_path.stat().st_size
    finally:
        tmp_path.unlink(missing_ok=True)

    print("TTS setup OK")
    print(f"  model : {config.piper_model}")
    print(f"  test  : synthesized {size} bytes of audio (not played)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="O.R.I.O.N. local text-to-speech (Piper).")
    parser.add_argument("--check", action="store_true", help="Verify setup and run a silent test.")
    parser.add_argument("--text", help="Text to speak. If omitted, text is read from stdin.")
    parser.add_argument("--out", type=Path, help="Save the audio to this WAV file.")
    parser.add_argument("--no-play", action="store_true", help="Do not play the audio aloud.")
    args = parser.parse_args(argv)

    try:
        if args.check:
            run_check()
            return 0

        text = args.text
        if text is None:
            if sys.stdin.isatty():
                print("[tts] ERROR: give text with --text or pipe it in.", file=sys.stderr)
                return 1
            text = sys.stdin.read()

        speak(text, out_path=args.out, play=not args.no_play)
    except TTSSetupError as exc:
        print(f"[tts] SETUP REQUIRED: {exc}", file=sys.stderr)
        return 2
    except (TTSRuntimeError, ValueError) as exc:
        print(f"[tts] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
