"""Fake O.R.I.O.N. backend for testing the HUD.

Sends a rotating state message to every client connected to ws://127.0.0.1:8765/events.
Stop it with Control + C to test the HUD's reconnect behavior.
"""
import asyncio
import itertools
import json

import websockets

SCRIPT = [
    ("Online", "Scanning the workspace"),
    ("Standby", "Waiting for input"),
    ("Online", "Thinking through your request"),
    ("Online", "Running a task"),
]


async def handler(websocket):
    print("HUD connected")
    try:
        for state, text in itertools.cycle(SCRIPT):
            message = {"state": state, "text": text, "source": "fake-backend"}
            await websocket.send(json.dumps(message))
            print("sent:", message)
            await asyncio.sleep(3)
    except websockets.ConnectionClosed:
        print("HUD disconnected")


async def main():
    async with websockets.serve(handler, "127.0.0.1", 8765):
        print("Fake backend listening on ws://127.0.0.1:8765/events")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
