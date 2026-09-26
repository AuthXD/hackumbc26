from __future__ import annotations

import asyncio
import json
import logging

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from .session import Session

log = logging.getLogger("teachback")
app = FastAPI(title="TeachBack")
session = Session()


class Hub:
    """Connected browser tabs. The newest tab is the active camera; every tab receives updates."""

    def __init__(self) -> None:
        self.clients: list[WebSocket] = []

    @property
    def active(self) -> WebSocket | None:
        return self.clients[-1] if self.clients else None

    async def broadcast(self, payload: dict) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json({**payload, "active": ws is self.active})
            except Exception:
                self.drop(ws)

    def drop(self, ws: WebSocket) -> None:
        if ws in self.clients:
            self.clients.remove(ws)


hub = Hub()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "mode": session.mode}


@app.get("/api/keyframes/{key}.jpg")
def keyframe(key: str) -> Response:
    data = session.keyframe(key)
    if data is None:
        raise HTTPException(404)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    hub.clients.append(ws)
    await hub.broadcast(session.snapshot())
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes"):
                if ws is not hub.active:
                    continue  # another tab owns the camera; this tab just watches
                snap = await asyncio.to_thread(session.process_frame, msg["bytes"])
                await hub.broadcast(snap)
            elif msg.get("text"):
                try:
                    cmd = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                if cmd.get("type") == "command":
                    snap = await asyncio.to_thread(session.command, str(cmd.get("action", "")))
                    await hub.broadcast(snap)
                elif cmd.get("type") == "calibrate":
                    snap = session.calibrate(str(cmd.get("color")), float(cmd.get("x", -1)), float(cmd.get("y", -1)))
                    await hub.broadcast(snap)
                elif cmd.get("type") == "reset_colors":
                    await hub.broadcast(session.reset_colors())
    except WebSocketDisconnect:
        pass
    finally:
        hub.drop(ws)
