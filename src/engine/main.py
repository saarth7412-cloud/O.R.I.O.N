"""
O.R.I.O.N. Engine: main.py
FastAPI orchestrator bound to 127.0.0.1:8765.

Endpoints:
  GET  /health    liveness + connected HUD client count
  POST /command   accepts {source, prompt, include_screen}
  WS   /events    broadcasts execution state events to the HUD
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

HOST = "127.0.0.1"
PORT = 8765

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
app = FastAPI(title="O.R.I.O.N. Engine", version="0.1.0")


# ---------------------------------------------------------------------------
# Command execution pipeline
# ---------------------------------------------------------------------------
async def execute_command(command_id: str, req: CommandRequest) -> str:
    """
    Runs one command through the pipeline. Screen context is captured and
    interpreted by the local VLM when requested. Automation dispatch is wired
    in a later step.
    """
    if not req.include_screen:
        await hub.broadcast({"type": "command.routing", "command_id": command_id})
        return "Command accepted. Automation dispatch is not yet wired."

    # Lazy import so the server boots before vision_core.py is present.
    from vision_core import capture_screen_base64, query_vlm

    await hub.broadcast({"type": "screen.capturing", "command_id": command_id})
    image_b64 = await asyncio.to_thread(capture_screen_base64)
    await hub.broadcast({"type": "screen.captured", "command_id": command_id})

    await hub.broadcast({"type": "vlm.querying", "command_id": command_id})
    answer = await asyncio.to_thread(query_vlm, req.prompt, image_b64)
    return answer


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
        output = await execute_command(command_id, req)
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


@app.websocket("/events")
async def events(ws: WebSocket) -> None:
    await hub.connect(ws)
    try:
        await ws.send_json({
            "type": "hello",
            "service": "orion-engine",
            "version": app.version,
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
