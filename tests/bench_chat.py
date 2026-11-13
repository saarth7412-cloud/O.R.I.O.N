#!/usr/bin/env python3
"""
O.R.I.O.N. chat model benchmark.

Compares the text model's factual accuracy, false-premise handling, and
latency. Run it against the live engine once per model, after changing
ORION_TEXT_MODEL and restarting the engine.

Usage:
    ORION_TEXT_MODEL=llama3.2:3b python tests/bench_chat.py
    ORION_TEXT_MODEL=qwen2.5:7b  python tests/bench_chat.py

The engine reads ORION_TEXT_MODEL, so the value you pass here must match
the value the engine was started with. This script prints it for reference.

Each case has a required keyword list. A reply passes if it contains at
least one of the keywords. Cases marked "reject" check that the model does
not agree with a false premise. Exit code is 0 unless the engine is unreachable.
"""
from __future__ import annotations

import argparse
import os
import time

import requests

# (question, accepted keywords, kind)
# kind "fact": reply must contain one keyword.
# kind "reject": reply must contain a correction keyword and must not start by agreeing.
CASES = [
    ("What is the capital of Japan?", ["tokyo"], "fact"),
    ("Who wrote Pride and Prejudice?", ["austen"], "fact"),
    ("What is the boiling point of water at sea level in Celsius?", ["100"], "fact"),
    ("How many days does it take Earth to orbit the Sun?", ["365"], "fact"),
    ("Which planet is closest to the Sun?", ["mercury"], "fact"),
    ("Earth takes 1,470 days to orbit the Sun, right?", ["365"], "reject"),
    ("The Great Wall of China is visible from the Moon with the naked eye, correct?",
     ["myth", "not visible", "not true", "isn't visible", "is not visible"], "reject"),
]

BENCH_NOTE = "Unknown-fact questions are printed for manual review, not auto-graded."
UNKNOWN_CASES = [
    "What was the population of a small village in Iceland in 1823?",
]


def ask(base: str, prompt: str) -> tuple[str, float, int]:
    start = time.monotonic()
    r = requests.post(
        f"{base}/command",
        json={"source": "bench", "prompt": prompt, "include_screen": False},
        timeout=120,
    )
    elapsed = time.monotonic() - start
    reply = (r.json().get("reply") or "") if r.ok else r.text[:200]
    return reply, elapsed, r.status_code


def main() -> int:
    parser = argparse.ArgumentParser(description="O.R.I.O.N. chat model benchmark")
    parser.add_argument("--port", type=int, default=int(os.environ.get("ORION_PORT", "8765")))
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    try:
        requests.get(f"{base}/health", timeout=5).raise_for_status()
    except requests.RequestException as exc:
        print(f"Engine not reachable at {base}: {exc}")
        return 1

    print(f"Chat model under test: {os.environ.get('ORION_TEXT_MODEL', 'llama3.2:3b (default)')}")
    print(f"{BENCH_NOTE}\n")

    passed = 0
    latencies: list[float] = []
    for question, keywords, kind in CASES:
        reply, elapsed, status = ask(base, question)
        latencies.append(elapsed)
        lowered = reply.lower()
        ok = status == 200 and any(k in lowered for k in keywords)
        if kind == "reject":
            ok = ok and not lowered.startswith(("yes", "correct", "that's right", "you're right"))
        passed += int(ok)
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {question}")
        print(f"       {reply[:200]}  ({elapsed:.1f}s)")

    print("\nUnknown-fact questions (review by hand):")
    for question in UNKNOWN_CASES:
        reply, elapsed, _ = ask(base, question)
        print(f"  Q: {question}")
        print(f"  A: {reply[:200]}  ({elapsed:.1f}s)")

    avg = sum(latencies) / len(latencies) if latencies else 0.0
    print(f"\nAccuracy: {passed} of {len(CASES)} graded cases passed")
    print(f"Average latency: {avg:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
