"""
O.R.I.O.N. Engine: main.py
FastAPI orchestrator. Binds to 127.0.0.1 on ORION_PORT (default 8765).

Endpoints:
  GET  /health    liveness + connected HUD client count
  POST /command   parses and routes one command, returns reply and action
  POST /state     voice bridge pushes a HUD state (listening, transcribing, ...)
  WS   /events    broadcasts execution events to the HUD

Routing, checked in this order:
  "help", "time", "date"         -> builtin, answered instantly, no model
  "open <App Name>"              -> automation.open_app()
  "shortcut <Name> | <input>"    -> automation.run_shortcut() ("| <input>" is optional)
  include_screen = true          -> vision_core: screenshot + vision model
  anything else                  -> chat_default: text-only model, no screenshot

HUD states:
  standby    resting state: sent on connect and after every command finishes
  thinking   a command has started
  executing  an app or Shortcut is running
  listening, transcribing, online  pushed by the voice bridge via POST /state

Ollama offline: returns HTTP 503 with {"reply": "Local language model is offline..."}.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from automation import open_app, run_shortcut
from errors import OLLAMA_OFFLINE_MESSAGE, OllamaUnavailableError
from text_core import query_text

HOST = "127.0.0.1"


def _read_port() -> int:
    raw = os.environ.get("ORION_PORT", "8765")
    try:
        port = int(raw)
    except ValueError as exc:
        raise SystemExit(f"ORION_PORT must be a whole number, got {raw!r}") from exc
    if not 1 <= port <= 65535:
        raise SystemExit(f"ORION_PORT must be between 1 and 65535, got {port}")
    return port


PORT = _read_port()

OPEN_PREFIX = "open "
SHORTCUT_PREFIX = "shortcut "
SHORTCUT_INPUT_SEPARATOR = "|"
BUILTIN_COMMANDS = {"help", "time", "date"}

ACTION_FOR_ROUTE = {
    "builtin": "builtin",
    "open_app": "open_app",
    "shortcut": "shortcut",
    "screen": "screen",
    "chat_default": "chat",
}

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
    reply: Optional[str] = None
    action: Optional[str] = None
    output: Optional[str] = None  # same text as reply, kept for older clients
    error: Optional[str] = None


class StateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str = Field(min_length=1, max_length=32)
    text: str = Field(default="", max_length=2000)
    source: str = Field(default="voice", min_length=1, max_length=64)


class CommandError(ValueError):
    """A command the engine can't understand or route. Maps to HTTP 400."""


@dataclass(frozen=True)
class Route:
    kind: Literal["builtin", "open_app", "shortcut", "screen", "chat_default"]
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
app = FastAPI(title="O.R.I.O.N. Engine", version="1.0.0")


async def set_state(command_id: str, state: str) -> None:
    await hub.broadcast({"type": "state", "state": state, "command_id": command_id})


@app.exception_handler(OllamaUnavailableError)
async def ollama_offline_handler(request: Request, exc: OllamaUnavailableError) -> JSONResponse:
    log.warning("Ollama unavailable: %s", exc)
    return JSONResponse(
        status_code=503,
        content={"status": "FAILED", "reply": OLLAMA_OFFLINE_MESSAGE, "action": None},
    )


# ---------------------------------------------------------------------------
# Built-in commands (instant, no model)
# ---------------------------------------------------------------------------
def run_builtin(name: str) -> str:
    now = datetime.now().astimezone()
    if name == "time":
        return now.strftime("%-I:%M %p")
    if name == "date":
        return now.strftime("%A, %B %-d, %Y")
    if name == "help":
        return (
            "Try: 'open <App Name>', 'shortcut <Name> | <input>', 'time', 'date', "
            "or ask any question. Add include_screen true to ask about your screen."
        )
    raise CommandError(f"Unknown built-in command: {name}")


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
def parse_route(req: CommandRequest) -> Route:
    """Decide what a command means. Raises CommandError if it can't be routed."""
    prompt = req.prompt.strip()
    lowered = prompt.lower()

    if lowered in BUILTIN_COMMANDS:
        return Route(kind="builtin", target=lowered)

    if lowered == OPEN_PREFIX.strip() or lowered.startswith(OPEN_PREFIX):
        app_name = prompt[len(OPEN_PREFIX):].strip()
        if not app_name:
            raise CommandError("'open' needs an app name, for example: open Finder")
        return Route(kind="open_app", target=app_name)

    if lowered == SHORTCUT_PREFIX.strip() or lowered.startswith(SHORTCUT_PREFIX):
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

    return Route(kind="chat_default", target=prompt)


async def execute_route(command_id: str, route: Route) -> str:
    """Run a routed command and return its text reply."""
    if route.kind == "builtin":
        return run_builtin(route.target)

    if route.kind == "open_app":
        await set_state(command_id, "executing")
        output = await asyncio.to_thread(open_app, route.target)
        return output or f"Opened {route.target}."

    if route.kind == "shortcut":
        await set_state(command_id, "executing")
        output = await asyncio.to_thread(run_shortcut, route.target, route.payload)
        return output or f"Shortcut '{route.target}' finished."

    if route.kind == "chat_default":
        # Conversational fallback: text-only model, no screenshot, no image.
        return await asyncio.to_thread(query_text, route.target)

    # Screen route. Lazy import so the server still boots if vision deps are broken.
    from vision_core import capture_screen_base64, query_vlm

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
    return {"status": "ok", "clients": hub.client_count, "port": PORT}


@app.post("/state")
async def post_state(req: StateRequest) -> dict:
    """Let the voice bridge push HUD states such as listening and transcribing."""
    await hub.broadcast({
        "type": "state",
        "state": req.state,
        "source": req.source,
        "text": req.text,
    })
    return {"status": "ok", "state": req.state}


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
        except OllamaUnavailableError:
            await hub.broadcast({
                "type": "command.failed",
                "command_id": command_id,
                "error": OLLAMA_OFFLINE_MESSAGE,
            })
            raise  # handled by ollama_offline_handler -> HTTP 503
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
        return CommandResponse(
            command_id=command_id,
            status="COMPLETED",
            reply=output,
            action=ACTION_FOR_ROUTE[route.kind],
            output=output,
        )
    finally:
        await set_state(command_id, "standby")


@app.websocket("/events")
async def events(ws: WebSocket) -> None:
    await hub.connect(ws)
    try:
        await ws.send_json({
            "type": "hello",
            "service": "orion-engine",
            "version": app.version,
            "state": "standby",
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
