"""
O.R.I.O.N. Engine: vision_core.py
In-memory macOS screen capture and local Ollama VLM querying.

Used by main.py via lazy import:
    from vision_core import capture_screen_base64, query_vlm
"""
from __future__ import annotations

import base64
import logging
import subprocess

import requests

log = logging.getLogger("orion.vision")

OLLAMA_URL = "http://localhost:11434/api/generate"
VLM_MODEL = "qwen2-vl"
VLM_TIMEOUT_SECONDS = 120
SCREENCAPTURE_TIMEOUT_SECONDS = 15
SCREENCAPTURE_CMD = ["screencapture", "-x", "-C", "-t", "jpg", "/dev/stdout"]


def capture_screen_base64() -> str:
    """
    Capture the main display in memory as JPEG and return it base64-encoded.
    -x suppresses the shutter sound, -C includes the cursor, and
    /dev/stdout keeps the image off disk.
    """
    try:
        result = subprocess.run(
            SCREENCAPTURE_CMD,
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

    if not result.stdout:
        raise RuntimeError(
            "screencapture returned no image data; check Screen Recording permission"
        )

    log.debug("captured %d bytes of JPEG", len(result.stdout))
    return base64.b64encode(result.stdout).decode("ascii")


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
        "images": [image_b64],
        "stream": False,
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=VLM_TIMEOUT_SECONDS)
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
