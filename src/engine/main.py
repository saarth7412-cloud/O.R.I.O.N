"""
O.R.I.O.N. Engine: main.py
FastAPI orchestrator bound to 127.0.0.1:8765.

Endpoints:
  GET  /health    liveness + connected HUD client count
  POST /command   parses and routes one command
  WS   /events    broadcasts execution events to the HUD

Command routing (the prompt field decides what happens):
  "open <App Name>"             -> automation.open_app()
  "shortcut <Name> | <input>"   -> automation.run_shortcut() ("| <input>" is optional)
  include_screen = true         -> vision_core: screenshot + Ollama VLM answer

HUD state events ({"type": "state", "state": ...}):
  thinking   parsing the command, or waiting on the vision model
  executing  running an AppleScript or Shortcut
  idle       the command finished, successfully or not
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from automation import open_app, run_shortcut

HOST = "127.0.0.1"
PORT = 8765

OPEN_PREFIX = "open "
SHORTCUT_PREFIX = "shortcut "
SHORTCUT_INPUT_SEPARATOR = "|"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("orion.engine")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=8000)
    include_screen: bool = False


class CommandResponse(BaseModel):
    command_id: str
    status: Literal["COMPLETED", "FAILED"]
    output: Optional[str] = None
    error: Optional[str] = None


class CommandError(ValueError):
    """A command the engine can't understand or route. Maps to HTTP 400."""


@dataclass(frozen=True)
class Route:
    kind: Literal["open_app", "shortcut", "screen"]
    target: str
    payload: Optional[str] = None


# ---------------------------------------------------------------------------
# HUD event broadcasting
# ---------------------------------------------------------------------------
class EventHub:
    """Tracks connected HUD WebSocket clients and fans out events to them."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
        log.info("HUD connected (%d total)", len(self._clients))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
        log.info("HUD disconnected (%d total)", len(self._clients))

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def broadcast(self, event: dict) -> None:
        payload = {**event, "ts": datetime.now(timezone.utc).isoformat()}
        async with self._lock:
            clients = list(self._clients)

        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001 - stale socket, drop it
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


hub = EventHub()
app = FastAPI(title="O.R.I.O.N. Engine", version="0.2.0")


async def set_state(
    command_id: str,
    state: Literal["thinking", "executing", "idle"],
) -> None:
    await hub.broadcast({"type": "state", "state": state, "command_id": command_id})


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
def parse_route(req: CommandRequest) -> Route:
    """Decide what a command means. Raises CommandError if it can't be routed."""
    prompt = req.prompt.strip()
    lowered = prompt.lower()

    if lowered.startswith(OPEN_PREFIX):
        app_name = prompt[len(OPEN_PREFIX):].strip()
        if not app_name:
            raise CommandError("'open' needs an app name, for example: open Finder")
        return Route(kind="open_app", target=app_name)

    if lowered.startswith(SHORTCUT_PREFIX):
        body = prompt[len(SHORTCUT_PREFIX):].strip()
        name, separator, shortcut_input = body.partition(SHORTCUT_INPUT_SEPARATOR)
        name = name.strip()
        if not name:
            raise CommandError(
                "'shortcut' needs a name, for example: shortcut My Shortcut | optional input"
            )
        payload = shortcut_input.strip() if separator else ""
        return Route(kind="shortcut", target=name, payload=payload or None)

    if req.include_screen:
        return Route(kind="screen", target=prompt)

    raise CommandError(
        "Unrecognized command. Try 'open <App Name>', 'shortcut <Name> | <input>', "
        "or send include_screen=true with a question about the screen."
    )


async def execute_route(command_id: str, route: Route) -> str:
    """Run a routed command and return its text output."""
    if route.kind == "open_app":
        await set_state(command_id, "executing")
        output = await asyncio.to_thread(open_app, route.target)
        return output or f"Opened {route.target}."

    if route.kind == "shortcut":
        await set_state(command_id, "executing")
        output = await asyncio.to_thread(run_shortcut, route.target, route.payload)
        return output or f"Shortcut '{route.target}' finished."

    # Screen route. Lazy import so the server still boots if vision deps are broken.
    from vision_core import capture_screen_base64, query_vlm

    await set_state(command_id, "thinking")
    await hub.broadcast({"type": "screen.capturing", "command_id": command_id})
    image_b64 = await asyncio.to_thread(capture_screen_base64)
    await hub.broadcast({"type": "screen.captured", "command_id": command_id})

    await hub.broadcast({"type": "vlm.querying", "command_id": command_id})
    return await asyncio.to_thread(query_vlm, route.target, image_b64)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "clients": hub.client_count}


@app.post("/command", response_model=CommandResponse)
async def submit_command(req: CommandRequest) -> CommandResponse:
    command_id = uuid.uuid4().hex
    log.info("command %s from %s (screen=%s)", command_id, req.source, req.include_screen)

    await hub.broadcast({
        "type": "command.received",
        "command_id": command_id,
        "source": req.source,
        "include_screen": req.include_screen,
    })

    try:
        await set_state(command_id, "thinking")

        try:
            route = parse_route(req)
            await hub.broadcast({
                "type": "command.routing",
                "command_id": command_id,
                "route": route.kind,
            })
            output = await execute_route(command_id, route)
        except CommandError as exc:
            await hub.broadcast({
                "type": "command.failed",
                "command_id": command_id,
                "error": str(exc),
            })
            raise HTTPException(
                status_code=400,
                detail={"command_id": command_id, "error": str(exc)},
            ) from exc
        except Exception as exc:  # noqa: BLE001
            log.exception("command %s failed", command_id)
            await hub.broadcast({
                "type": "command.failed",
                "command_id": command_id,
                "error": str(exc),
            })
            raise HTTPException(
                status_code=500,
                detail={"command_id": command_id, "error": str(exc)},
            ) from exc

        await hub.broadcast({
            "type": "command.completed",
            "command_id": command_id,
            "output": output,
        })
        return CommandResponse(command_id=command_id, status="COMPLETED", output=output)
    finally:
        await set_state(command_id, "idle")


@app.websocket("/events")
async def events(ws: WebSocket) -> None:
    await hub.connect(ws)
    try:
        await ws.send_json({
            "type": "hello",
            "service": "orion-engine",
            "version": app.version,
            "state": "idle",
        })
        # Client messages are ignored; the loop exists to detect disconnects.
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(ws)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=HOST, port=PORT, log_level="info")
