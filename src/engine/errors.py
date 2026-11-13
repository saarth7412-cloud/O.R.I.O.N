"""
O.R.I.O.N. Engine: errors.py
Shared exception types.

OllamaUnavailableError means Ollama isn't running or can't be reached.
main.py turns it into HTTP 503 with a message the voice client can speak.
"""
from __future__ import annotations


OLLAMA_OFFLINE_MESSAGE = "Local language model is offline. Please start Ollama."


class OllamaUnavailableError(RuntimeError):
    """Ollama is not running or the connection was refused."""
