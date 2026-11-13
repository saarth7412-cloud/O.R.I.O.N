"""
O.R.I.O.N. Engine: vision_core.py
Screen capture and local Ollama VLM querying.

Capture approach (approved Option 1):
  macOS `screencapture` cannot write image data to /dev/stdout on this
  machine, so it writes to a short-lived temp file in /tmp. The image is
  shrunk with macOS `sips`, read into memory, base64-encoded, and the temp
  file is deleted in a `finally` block, so the image never persists past
  the capture call.

Why the downscale: a full-resolution screenshot is several megapixels, and
the vision model has to process all of it on every request. Capping the
longest edge keeps the text readable while cutting response time sharply.

Used by main.py via lazy import:
    from vision_core import capture_screen_base64, query_vlm
"""
from __future__ import annotations

import base64
import logging
import os
import subprocess
import tempfile

import requests

from errors import OllamaUnavailableError
from prompts import SYSTEM_PROMPT

log = logging.getLogger("orion.vision")

OLLAMA_URL = "http://localhost:11434/api/generate"
VLM_MODEL = "qwen2-vl"
VLM_TIMEOUT_SECONDS = 180
SCREENCAPTURE_TIMEOUT_SECONDS = 15
SIPS_TIMEOUT_SECONDS = 15
TEMP_DIR = "/tmp"

# Longest edge in pixels for the image sent to the model.
def _read_max_dim() -> int:
    """Longest image edge in pixels. Set ORION_VISION_MAX_DIM to override (default 768)."""
    raw = os.environ.get("ORION_VISION_MAX_DIM", "768")
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"ORION_VISION_MAX_DIM must be a whole number, got {raw!r}") from exc
    if value < 64:
        raise RuntimeError(f"ORION_VISION_MAX_DIM must be at least 64, got {value}")
    return value


MAX_IMAGE_EDGE_PX = _read_max_dim()


def _downscale_in_place(path: str) -> None:
    """Shrink the image so its longest edge is MAX_IMAGE_EDGE_PX, keeping aspect ratio."""
    try:
        result = subprocess.run(
            ["sips", "-Z", str(MAX_IMAGE_EDGE_PX), path],
            capture_output=True,
            timeout=SIPS_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("sips not found; this requires macOS") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("sips timed out while resizing the screenshot") from exc

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"sips failed to resize the screenshot (exit {result.returncode}): {stderr or 'no stderr'}"
        )


def capture_screen_base64() -> str:
    """
    Capture the main display as JPEG via a transient /tmp file, shrink it,
    and return the image base64-encoded. -x suppresses the shutter sound
    and -C includes the cursor. The temp file is always removed before
    returning or raising.
    """
    fd, tmp_path = tempfile.mkstemp(prefix="orion_capture_", suffix=".jpg", dir=TEMP_DIR)
    os.close(fd)

    try:
        try:
            result = subprocess.run(
                ["screencapture", "-x", "-C", "-t", "jpg", tmp_path],
                capture_output=True,
                timeout=SCREENCAPTURE_TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("screencapture binary not found; this requires macOS") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("screencapture timed out") from exc

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"screencapture failed (exit {result.returncode}): {stderr or 'no stderr'}"
            )

        if os.path.getsize(tmp_path) == 0:
            raise RuntimeError(
                "screencapture produced an empty image; check Screen Recording permission"
            )

        _downscale_in_place(tmp_path)

        with open(tmp_path, "rb") as fh:
            image_bytes = fh.read()

        if not image_bytes:
            raise RuntimeError("resized screenshot is empty")

        log.debug("captured %d bytes of JPEG after resize", len(image_bytes))
        return base64.b64encode(image_bytes).decode("ascii")
    finally:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass


def query_vlm(prompt: str, image_b64: str) -> str:
    """
    Send a prompt and a base64 JPEG to the local Ollama VLM and return its text reply.
    """
    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    if not image_b64:
        raise ValueError("image_b64 must not be empty")

    payload = {
        "model": VLM_MODEL,
        "prompt": prompt,
        "system": SYSTEM_PROMPT,
        "images": [image_b64],
        "stream": False,
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=VLM_TIMEOUT_SECONDS)
    except requests.ConnectionError as exc:
        raise OllamaUnavailableError(f"Cannot reach Ollama at {OLLAMA_URL}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Ollama request failed at {OLLAMA_URL}: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"Ollama returned HTTP {response.status_code}: {response.text[:500]}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("Ollama returned a non-JSON response") from exc

    text = data.get("response")
    if not isinstance(text, str):
        raise RuntimeError("Ollama response is missing the 'response' field")

    log.debug("VLM responded with %d characters", len(text))
    return text.strip()
