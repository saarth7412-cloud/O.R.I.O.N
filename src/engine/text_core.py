"""
O.R.I.O.N. Engine: text_core.py
Fast text-only answers from a small local Ollama model.

Used for plain commands (no include_screen) that aren't built-ins or
app/Shortcut commands. No screenshot is taken and no image is sent, so
this path is much quicker than the vision model.

Used by main.py via lazy import:
    from text_core import query_text
"""
from __future__ import annotations

import logging

import requests

from prompts import SYSTEM_PROMPT

log = logging.getLogger("orion.text")

OLLAMA_URL = "http://localhost:11434/api/generate"
TEXT_MODEL = "llama3.2:3b"
TEXT_TIMEOUT_SECONDS = 60

# Caps reply length so short questions come back quickly.
MAX_REPLY_TOKENS = 256


def query_text(prompt: str) -> str:
    """Send a text-only prompt to the small local model and return its reply."""
    if not prompt.strip():
        raise ValueError("prompt must not be empty")

    payload = {
        "model": TEXT_MODEL,
        "system": SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": MAX_REPLY_TOKENS},
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=TEXT_TIMEOUT_SECONDS)
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

    log.debug("text model responded with %d characters", len(text))
    return text.strip()
