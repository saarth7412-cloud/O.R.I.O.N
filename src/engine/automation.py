"""
O.R.I.O.N. Engine: automation.py
Local macOS task execution through AppleScript (osascript) and Shortcuts
(shortcuts run).

Safety notes:
  * Scripts and shortcut names are never built through a shell. Every call
    uses an argument list with shell=False.
  * App and shortcut names are validated against a conservative character
    set before use.
  * Free text that goes into AppleScript is escaped before interpolation.

Used by main.py via:
    from automation import run_applescript, run_shortcut, open_app
"""
from __future__ import annotations

import logging
import re
import subprocess

log = logging.getLogger("orion.automation")

OSASCRIPT_CMD = "osascript"
SHORTCUTS_CMD = "shortcuts"
DEFAULT_TIMEOUT_SECONDS = 30

# Letters, digits, spaces, dots, hyphens, underscores, and parentheses.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9 ._()\-]{1,100}$")


class AutomationError(RuntimeError):
    """Raised when a local automation step fails or receives bad input."""


def _validate_name(name: str, kind: str) -> str:
    cleaned = name.strip()
    if not _SAFE_NAME.match(cleaned):
        raise AutomationError(
            f"{kind} name {name!r} contains unsupported characters or is too long"
        )
    return cleaned


def escape_applescript_string(text: str) -> str:
    """Escape backslashes and double quotes so text can sit inside an AppleScript string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def run_applescript(script: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """
    Run an AppleScript source string via osascript and return its stdout.
    The script is sent on stdin, so no shell quoting is involved.
    """
    if not script.strip():
        raise AutomationError("AppleScript source must not be empty")

    try:
        result = subprocess.run(
            [OSASCRIPT_CMD, "-"],
            input=script.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AutomationError("osascript not found; this requires macOS") from exc
    except subprocess.TimeoutExpired as exc:
        raise AutomationError(f"AppleScript timed out after {timeout}s") from exc

    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    stderr = result.stderr.decode("utf-8", errors="replace").strip()

    if result.returncode != 0:
        raise AutomationError(
            f"AppleScript failed (exit {result.returncode}): {stderr or 'no stderr'}"
        )

    log.info("AppleScript completed")
    return stdout


def open_app(app_name: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Bring a macOS application to the foreground, launching it if needed."""
    safe_name = _validate_name(app_name, "application")
    script = f'tell application "{escape_applescript_string(safe_name)}" to activate'
    return run_applescript(script, timeout=timeout)


def run_shortcut(
    shortcut_name: str,
    input_text: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """
    Run a macOS Shortcut by name and return its output text.
    If input_text is given, it is passed to the shortcut as its input.
    """
    safe_name = _validate_name(shortcut_name, "shortcut")

    stdin_bytes = input_text.encode("utf-8") if input_text is not None else None

    try:
        result = subprocess.run(
            [SHORTCUTS_CMD, "run", safe_name],
            input=stdin_bytes,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AutomationError("shortcuts CLI not found; this requires macOS 12 or later") from exc
    except subprocess.TimeoutExpired as exc:
        raise AutomationError(f"Shortcut {safe_name!r} timed out after {timeout}s") from exc

    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    stderr = result.stderr.decode("utf-8", errors="replace").strip()

    if result.returncode != 0:
        raise AutomationError(
            f"Shortcut {safe_name!r} failed (exit {result.returncode}): {stderr or 'no stderr'}"
        )

    log.info("Shortcut %r completed", safe_name)
    return stdout
