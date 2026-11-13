#!/usr/bin/env python3
"""
O.R.I.O.N. v1 Definition of Done verification.

Runs against a live engine. Start the engine first with:
    scripts/start_orion.sh

Usage:
    python tests/verify_v1.py              core checks
    python tests/verify_v1.py --screen     also run a live screen-context question
                                           (needs Screen Recording permission and Ollama)
    python tests/verify_v1.py --port 8765  check a specific port

Checks:
  - /health responds ok
  - built-in commands (time, date) answer instantly
  - bare "open" and "shortcut" return HTTP 400
  - chat fallback returns {status, reply, action}, or a clean 503 if Ollama is offline
  - WebSocket hello uses "standby", and no event ever uses "idle"
  - a command broadcasts thinking, then standby
  - POST /state broadcasts to the HUD
  - (--screen) screen-context request returns a non-empty reply

Exit code 0 means every check passed or was skipped. 1 means at least one failed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import requests
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

OFFLINE_MESSAGE = "Local language model is offline. Please start Ollama."

results: list[tuple[str, str]] = []


def record(name: str, ok: bool, detail: str = "", skip: bool = False) -> None:
    status = "SKIP" if skip else ("PASS" if ok else "FAIL")
    results.append((name, status))
    suffix = f"  ({detail})" if detail else ""
    print(f"[{status}] {name}{suffix}")


def post_command(base: str, prompt: str, include_screen: bool = False, timeout: float = 120) -> requests.Response:
    return requests.post(
        f"{base}/command",
        json={"source": "verify", "prompt": prompt, "include_screen": include_screen},
        timeout=timeout,
    )


def collect_events(ws, seconds: float) -> list[dict]:
    """Read every WebSocket message that arrives within `seconds`."""
    events: list[dict] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            raw = ws.recv(timeout=0.2)
        except TimeoutError:
            continue
        except ConnectionClosed:
            break
        events.append(json.loads(raw))
    return events


def state_values(events: list[dict]) -> list[str]:
    return [e["state"] for e in events if e.get("type") == "state" and "state" in e]


def check_health(base: str) -> bool:
    r = requests.get(f"{base}/health", timeout=5)
    body = r.json() if r.ok else {}
    ok = r.status_code == 200 and body.get("status") == "ok"
    record("health endpoint responds ok", ok, f"HTTP {r.status_code}")
    return ok


def check_builtins(base: str) -> None:
    for word in ("time", "date"):
        start = time.monotonic()
        r = post_command(base, word)
        elapsed = time.monotonic() - start
        body = r.json() if r.ok else {}
        ok = (
            r.status_code == 200
            and body.get("status") == "COMPLETED"
            and bool(body.get("reply"))
            and body.get("action") == "builtin"
        )
        record(f"built-in '{word}' answers instantly", ok and elapsed < 2,
               f"{elapsed:.2f}s, reply={body.get('reply')!r}")


def check_bare_commands_rejected(base: str) -> None:
    for word in ("open", "shortcut"):
        r = post_command(base, word)
        record(f"bare '{word}' returns HTTP 400", r.status_code == 400, f"HTTP {r.status_code}")


def check_chat_fallback(base: str) -> None:
    r = post_command(base, "What is the capital of Japan?")
    if r.status_code == 503:
        body = r.json()
        ok = body.get("reply") == OFFLINE_MESSAGE
        record("chat fallback: Ollama offline returns clean 503", ok,
               "start Ollama to test the live answer")
        return
    body = r.json() if r.ok else {}
    keys_ok = all(k in body for k in ("status", "reply", "action"))
    answer_ok = "tokyo" in (body.get("reply") or "").lower()
    record("chat fallback returns {status, reply, action}", r.status_code == 200 and keys_ok and answer_ok,
           f"HTTP {r.status_code}, action={body.get('action')!r}")


def check_websocket(base: str) -> None:
    ws_url = base.replace("http://", "ws://") + "/events"
    try:
        with connect(ws_url, open_timeout=5) as ws:
            hello = json.loads(ws.recv(timeout=5))
            record("WebSocket hello uses 'standby'", hello.get("state") == "standby",
                   f"state={hello.get('state')!r}")

            # A command should broadcast thinking, then standby.
            post_command(base, "time")
            command_events = collect_events(ws, 1.5)
            states = state_values(command_events)
            first_thinking = states.index("thinking") if "thinking" in states else -1
            last_standby = (len(states) - 1 - states[::-1].index("standby")) if "standby" in states else -1
            ordered = first_thinking != -1 and last_standby > first_thinking
            record("command broadcasts thinking then standby", ordered, f"states={states}")

            # POST /state should reach the HUD straight away.
            requests.post(f"{base}/state",
                          json={"state": "listening", "text": "", "source": "voice"}, timeout=5)
            state_events = collect_events(ws, 1.0)
            heard = any(e.get("state") == "listening" and e.get("source") == "voice" for e in state_events)
            record("POST /state broadcasts to the HUD", heard)

            all_events = [hello] + command_events + state_events
            idle_seen = any(e.get("state") == "idle" for e in all_events)
            record("no event ever uses 'idle'", not idle_seen)
    except (OSError, ConnectionClosed, TimeoutError) as exc:
        record("WebSocket /events reachable", False, str(exc))


def check_screen(base: str) -> None:
    r = post_command(base, "In one sentence, what app is in the foreground?", include_screen=True, timeout=180)
    body = r.json() if r.ok else {}
    reply = (body.get("reply") or "").strip()
    ok = r.status_code == 200 and bool(reply) and body.get("action") == "screen"
    detail = f"HTTP {r.status_code}, reply={reply[:90]!r}" if reply else f"HTTP {r.status_code}, body={r.text[:160]!r}"
    record("screen-context request returns a reply", ok, detail)


def main() -> int:
    parser = argparse.ArgumentParser(description="O.R.I.O.N. v1 verification")
    parser.add_argument("--port", type=int, default=int(os.environ.get("ORION_PORT", "8765")))
    parser.add_argument("--screen", action="store_true", help="also run a live screen-context check")
    args = parser.parse_args()

    base = f"http://127.0.0.1:{args.port}"
    print(f"Verifying O.R.I.O.N. engine at {base}\n")

    try:
        healthy = check_health(base)
    except requests.RequestException as exc:
        print(f"[FAIL] Engine not reachable at {base}: {exc}")
        print("Start it first with: scripts/start_orion.sh")
        return 1

    if healthy:
        check_builtins(base)
        check_bare_commands_rejected(base)
        check_chat_fallback(base)
        check_websocket(base)
        if args.screen:
            check_screen(base)
        else:
            record("screen-context request (run with --screen)", False, skip=True)

    passed = sum(1 for _, s in results if s == "PASS")
    failed = sum(1 for _, s in results if s == "FAIL")
    skipped = sum(1 for _, s in results if s == "SKIP")
    print(f"\nSummary: {passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
