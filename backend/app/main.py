from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel

from .config import settings
from .integrations import ElevenLabsVoice, GeminiDescriber
from .session import Session

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("teachback")

session = Session()
gemini = GeminiDescriber(settings)
voice = ElevenLabsVoice(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()

    def history_changed() -> None:  # runs on the history writer thread
        loop.call_soon_threadsafe(lambda: asyncio.create_task(hub.broadcast(session.snapshot())))

    session.history_writer.on_change = history_changed
    yield
    session.history_writer.on_change = None
    await asyncio.to_thread(session.close)  # drains accepted history events within a bounded timeout


app = FastAPI(title="TeachBack", lifespan=lifespan)


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


async def describe_pending_steps() -> None:
    """Ask Gemini to word newly learned steps in the background; the UI updates when it answers."""
    pending = session.take_pending_ai()
    if not gemini.enabled:
        return
    for index, after_image in pending:
        asyncio.create_task(_describe(index, after_image))


async def _describe(index: int, after_image: str | None) -> None:
    found = session.step_for_ai(index, after_image)
    if not found:
        return
    step, before, after = found
    text = await gemini.describe(step, before, after)
    if text and session.set_ai_description(index, after_image, text):
        log.info("Gemini named step %d: %s", index + 1, text.title)
        await hub.broadcast(session.snapshot())


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "mode": session.mode, "gemini": gemini.enabled, "elevenlabs": voice.enabled,
            "detector": session.detector_status(), "storage": session.setups.status().to_json(),
            "history": {**session.history.status().to_json(), "writer": session.history_writer.stats()}}


@app.get("/api/keyframes/{key}.jpg")
def keyframe(key: str) -> Response:
    data = session.keyframe(key)
    if data is None:
        raise HTTPException(404)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


class SpeakRequest(BaseModel):
    text: str


@app.post("/api/speak")
async def speak(req: SpeakRequest) -> Response:
    """ElevenLabs proxy (keeps the key server-side). 503 tells the browser to use speechSynthesis."""
    audio = await voice.speak(req.text[:500])
    if audio is None:
        raise HTTPException(503, "Voice unavailable")
    return Response(audio, media_type="audio/mpeg")


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
                kind = cmd.get("type")
                if kind == "command":
                    snap = await asyncio.to_thread(session.command, str(cmd.get("action", "")))
                elif kind == "calibrate":
                    snap = session.calibrate(str(cmd.get("color")), float(cmd.get("x", -1)), float(cmd.get("y", -1)))
                elif kind == "reset_colors":
                    snap = session.reset_colors()
                elif kind == "detector":
                    snap = session.configure_detector(str(cmd.get("kind", "")), str(cmd.get("labels", "")))
                elif kind == "workspace":
                    snap = session.set_workspace(str(cmd.get("workspace", "")))
                elif kind == "setup_capture":
                    snap = await asyncio.to_thread(session.capture_setup, str(cmd.get("name", "")))
                elif kind == "setup_select":
                    snap = session.select_setup(str(cmd.get("id", "")))
                elif kind == "setup_check":
                    snap = session.check_setup()
                elif kind == "setup_refresh":
                    snap = await asyncio.to_thread(session.refresh_setups)
                elif kind == "history_refresh":
                    snap = session.refresh_history()
                else:
                    continue
                await hub.broadcast(snap)
            await describe_pending_steps()
    except WebSocketDisconnect:
        pass
    finally:
        hub.drop(ws)
