#!/usr/bin/env python3
"""
stt_wrapper.py
Project O.R.I.O.N. | agent-voice
Records short microphone clips and transcribes them locally via whisper.cpp (Metal).

Usage:
    python stt_wrapper.py --check          # verify setup only
    python stt_wrapper.py --seconds 5      # record 5 s, print transcript to stdout
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass
from typing import Callable
from pathlib import Path

import numpy as np

try:
    import sounddevice as sd
except ImportError:  # reported cleanly by ensure_ready()
    sd = None


SAMPLE_RATE = 16_000          # Whisper expects 16 kHz mono
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2        # int16
DEFAULT_SECONDS = 5.0
SILENCE_PEAK_THRESHOLD = 200  # int16 peak below this is treated as silence
TRANSCRIBE_TIMEOUT_S = 60
DEFAULT_THREADS = min(os.cpu_count() or 4, 8)

ORION_ROOT = Path(os.environ.get("ORION_ROOT", Path.home() / "orion"))
VOICE_DIR = Path(__file__).resolve().parent
ENV_FILE = VOICE_DIR / "voice.env"

DEFAULT_WHISPER_CLI = ORION_ROOT / "vendor" / "whisper.cpp" / "build" / "bin" / "whisper-cli"
DEFAULT_WHISPER_MODEL = ORION_ROOT / "models" / "ggml-base.en.bin"
DEFAULT_WHISPER_DIR = ORION_ROOT / "vendor" / "whisper.cpp"

# Whisper emits tokens such as [BLANK_AUDIO], (music), *sigh* for non-speech audio.
NON_SPEECH_PATTERN = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*")


class STTSetupError(RuntimeError):
    """Raised when the STT pipeline is missing a dependency. Message contains the fix."""


class STTRuntimeError(RuntimeError):
    """Raised when recording or transcription fails at runtime."""


@dataclass(frozen=True)
class STTConfig:
    whisper_cli: Path
    whisper_model: Path
    threads: int


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


def load_config() -> STTConfig:
    """Process environment overrides voice.env, which overrides built-in defaults."""
    file_values = _read_env_file(ENV_FILE)

    def pick(key: str, default: Path | str) -> str:
        return os.environ.get(key) or file_values.get(key) or str(default)

    threads_raw = os.environ.get("ORION_STT_THREADS", "")
    threads = int(threads_raw) if threads_raw.isdigit() else DEFAULT_THREADS

    return STTConfig(
        whisper_cli=Path(pick("ORION_WHISPER_CLI", DEFAULT_WHISPER_CLI)).expanduser(),
        whisper_model=Path(pick("ORION_WHISPER_MODEL", DEFAULT_WHISPER_MODEL)).expanduser(),
        threads=threads,
    )


def ensure_ready(config: STTConfig) -> None:
    """Validate every dependency before recording. Raises STTSetupError with the fix."""
    if sys.platform != "darwin":
        raise STTSetupError("STT workspace targets macOS only.")

    if sd is None:
        raise STTSetupError(
            "Python package 'sounddevice' is missing.\n"
            f"Fix: {Path.home() / '.orion-venv' / 'bin' / 'python'} -m pip install sounddevice numpy"
        )

    if not config.whisper_cli.is_file() or not os.access(config.whisper_cli, os.X_OK):
        raise STTSetupError(
            f"whisper-cli not found or not executable at: {config.whisper_cli}\n"
            f"Fix: bash {VOICE_DIR / 'setup_voice_env.sh'}"
        )

    if not config.whisper_model.is_file():
        raise STTSetupError(
            f"Whisper model not found at: {config.whisper_model}\n"
            f"Fix: bash {DEFAULT_WHISPER_DIR / 'models' / 'download-ggml-model.sh'} base.en && "
            f"cp {DEFAULT_WHISPER_DIR / 'models' / 'ggml-base.en.bin'} {config.whisper_model}"
        )


def _check_input_device() -> None:
    try:
        sd.query_devices(kind="input")
    except Exception as exc:  # PortAudio raises several exception types
        raise STTSetupError(
            "No usable microphone input device was found.\n"
            "Fix: check System Settings → Sound → Input, and confirm Terminal has "
            "Microphone access under System Settings → Privacy & Security → Microphone."
        ) from exc


def record_clip(seconds: float = DEFAULT_SECONDS) -> np.ndarray:
    """Record mono int16 audio at SAMPLE_RATE. Returns array shaped (frames, 1)."""
    if seconds <= 0 or seconds > 30:
        raise ValueError("seconds must be between 0 and 30 for a single clip.")
    _check_input_device()
    frames = int(seconds * SAMPLE_RATE)
    try:
        audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16")
        sd.wait()
    except Exception as exc:
        raise STTRuntimeError(
            f"Microphone capture failed: {exc}\n"
            "Fix: grant Microphone access to Terminal under System Settings → "
            "Privacy & Security → Microphone, then retry."
        ) from exc
    return audio


def write_wav(path: Path, audio: np.ndarray) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(np.ascontiguousarray(audio, dtype=np.int16).tobytes())


def clean_transcript(raw: str) -> str:
    """Remove non-speech tokens, collapse whitespace, return a payload-ready string."""
    without_tags = NON_SPEECH_PATTERN.sub(" ", raw)
    return re.sub(r"\s+", " ", without_tags).strip()


def transcribe_file(wav_path: Path, config: STTConfig | None = None) -> str:
    """Transcribe an existing WAV file (16 kHz mono) through whisper-cli."""
    config = config or load_config()
    ensure_ready(config)

    command = [
        str(config.whisper_cli),
        "-m", str(config.whisper_model),
        "-f", str(wav_path),
        "-l", "en",
        "-t", str(config.threads),
        "-nt",   # no timestamps
        "-np",   # no progress prints
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=TRANSCRIBE_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise STTRuntimeError(
            f"whisper-cli exceeded {TRANSCRIBE_TIMEOUT_S}s on {wav_path.name}."
        ) from exc

    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-5:]
        raise STTRuntimeError(
            f"whisper-cli exited with code {result.returncode}.\n" + "\n".join(tail)
        )

    return clean_transcript(result.stdout)


def _notify(on_stage, stage: str) -> None:
    """Report a pipeline stage to an optional callback. Callback errors never stop audio."""
    if on_stage is None:
        return
    try:
        on_stage(stage)
    except Exception:
        pass


def listen_and_transcribe(
    seconds: float = DEFAULT_SECONDS,
    on_stage: Callable[[str], None] | None = None,
) -> str:
    """Record one clip from the microphone and return its cleaned transcript.

    on_stage, if given, is called with 'listening' before capture and
    'transcribing' before Whisper runs.
    """
    config = load_config()
    ensure_ready(config)

    _notify(on_stage, "listening")
    audio = record_clip(seconds)
    peak = int(np.max(np.abs(audio))) if audio.size else 0
    if peak < SILENCE_PEAK_THRESHOLD:
        return ""

    fd, tmp_name = tempfile.mkstemp(prefix="orion_stt_", suffix=".wav")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        write_wav(tmp_path, audio)
        _notify(on_stage, "transcribing")
        return transcribe_file(tmp_path, config)
    finally:
        tmp_path.unlink(missing_ok=True)


def run_check() -> None:
    config = load_config()
    ensure_ready(config)
    _check_input_device()
    print("STT setup OK")
    print(f"  whisper-cli : {config.whisper_cli}")
    print(f"  model       : {config.whisper_model}")
    print(f"  threads     : {config.threads}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="O.R.I.O.N. local speech-to-text.")
    parser.add_argument("--check", action="store_true", help="Verify setup without recording.")
    parser.add_argument(
        "--seconds", type=float, default=DEFAULT_SECONDS,
        help=f"Clip length in seconds (default {DEFAULT_SECONDS}).",
    )
    args = parser.parse_args(argv)

    try:
        if args.check:
            run_check()
            return 0
        transcript = listen_and_transcribe(args.seconds)
    except STTSetupError as exc:
        print(f"[stt] SETUP REQUIRED: {exc}", file=sys.stderr)
        return 2
    except (STTRuntimeError, ValueError) as exc:
        print(f"[stt] ERROR: {exc}", file=sys.stderr)
        return 1

    print(transcript)
    return 0


if __name__ == "__main__":
    sys.exit(main())
