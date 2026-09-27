from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel

from .config import settings
from .integrations import ElevenLabsVoice, GeminiDescriber
from .pairing import phone_link
from .session import Session
from .sources import SourceRegistry

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

    def detector_changed() -> None:
        loop.call_soon_threadsafe(lambda: asyncio.create_task(hub.broadcast(session.snapshot())))

    session.on_detector_change = detector_changed
    watchdog = asyncio.create_task(hub.watch_sources())
    yield
    watchdog.cancel()
    session.history_writer.on_change = None
    session.on_detector_change = None
    await asyncio.to_thread(session.close)  # drains accepted history events within a bounded timeout


app = FastAPI(title="TeachBack", lifespan=lifespan)


class Hub:
    """Connected pages. The newest page that is *delivering valid frames* owns the camera (see sources.py);
    every page receives updates plus its own flags (may it stream, is it the owner, should it speak)."""

    def __init__(self) -> None:
        self.clients: list[WebSocket] = []
        self.sources = SourceRegistry()
        self._broadcast_lock = asyncio.Lock()

    def add(self, ws: WebSocket) -> None:
        self.clients.append(ws)
        self.sources.connect(ws)

    def camera_signature(self) -> tuple:
        now = time.monotonic()
        owner = self.sources.owner(now)
        return (id(owner.key) if owner else None, self.sources.phone_state(now),
                tuple(self.sources.may_stream(ws, now) for ws in self.clients))

    async def broadcast(self, payload: dict) -> None:
        async with self._broadcast_lock:
            now = time.monotonic()
            camera = self.sources.status(now)
            for ws in list(self.clients):
                try:
                    await ws.send_json({**payload, "camera": camera, **self.sources.client_view(ws, now)})
                except Exception:
                    self.drop(ws)

    async def watch_sources(self, interval: float = 0.5) -> None:
        """A source that stops sending without disconnecting (phone locked, tab hidden) loses the camera;
        tell every page so the laptop resumes its own feed."""
        last = self.camera_signature()
        while True:
            await asyncio.sleep(interval)
            current = self.camera_signature()
            if current != last:
                last = current
                await self.broadcast(session.snapshot())

    async def broadcast_frame(self, jpeg: bytes, sender: WebSocket) -> None:
        """Mirror the active camera frame to viewer tabs without echoing it to the phone."""
        async with self._broadcast_lock:
            for ws in list(self.clients):
                if ws is sender:
                    continue
                try:
                    await ws.send_bytes(jpeg)
                except Exception:
                    self.drop(ws)

    def drop(self, ws: WebSocket) -> None:
        if ws in self.clients:
            self.clients.remove(ws)
        self.sources.disconnect(ws)


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
            "history": {**session.history.status().to_json(), "writer": session.history_writer.stats()},
            "procedures": session.procedures.status().to_json()}


@app.get("/api/phone-link")
def get_phone_link() -> dict:
    return phone_link(settings.phone_public_url)


@app.get("/api/mat-view.jpg")
def mat_view() -> Response:
    """Latest stabilized top-down mat image (latest only; the page asks when viewSeq changes)."""
    data = session.mat_view_jpeg
    if data is None:
        raise HTTPException(404)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


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
    hub.add(ws)
    await hub.broadcast(session.snapshot())
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes"):
                data = msg["bytes"]
                if not hub.sources.frame_received(ws, data, time.monotonic()):
                    continue  # another page owns the camera (or this is not a JPEG); this page just watches
                kind = hub.sources.source_kind(ws)
                snap = await asyncio.to_thread(session.process_frame, data, None, kind)
                if snap.get("error"):
                    hub.sources.frame_invalid(ws)  # undecodable: never counts as streaming
                    snap = session.snapshot()
                else:
                    await hub.broadcast_frame(data, ws)
                await hub.broadcast(snap)
            elif msg.get("text"):
                try:
                    cmd = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                kind = cmd.get("type")
                if kind == "hello":
                    hub.sources.hello(ws, str(cmd.get("role", "")), str(cmd.get("kind", "")))
                    snap = session.snapshot()
                elif kind == "source":
                    hub.sources.set_kind(ws, str(cmd.get("kind", "")))
                    snap = session.snapshot()
                elif kind == "camera_status":
                    hub.sources.camera_status(ws, str(cmd.get("state", "")), str(cmd.get("message", "")))
                    snap = session.snapshot()
                elif kind == "mat_calibrate":
                    snap = await asyncio.to_thread(session.calibrate_mat, cmd.get("points"))
                elif kind == "mat_clear":
                    snap = session.clear_mat()
                elif kind == "command":
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
                elif kind == "procedure_save":
                    summary = cmd.get("summary")
                    snap = await asyncio.to_thread(session.save_procedure, str(cmd.get("name", "")),
                                                   None if summary is None else str(summary))
                elif kind == "procedure_load":
                    snap = session.load_procedure(str(cmd.get("id", "")))
                elif kind == "procedure_refresh":
                    snap = await asyncio.to_thread(session.refresh_procedures)
                else:
                    continue
                await hub.broadcast(snap)
            await describe_pending_steps()
    except WebSocketDisconnect:
        pass
    finally:
        hub.drop(ws)
        await hub.broadcast(session.snapshot())
