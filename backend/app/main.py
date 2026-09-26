from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .config import settings
from .vision import MotionMeter, analyze_frame, decode_jpeg, downscale

app = FastAPI(title="TeachBack")


def zones_json() -> list[dict]:
    return [{"id": z.id, "label": z.label, "x": z.x, "y": z.y, "w": z.w, "h": z.h} for z in settings.vision.zones]


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    meter = MotionMeter()
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if not data:
                continue
            t0 = time.perf_counter()
            frame = await asyncio.to_thread(decode_jpeg, data)
            if frame is None:
                await ws.send_json({"type": "update", "error": "bad frame"})
                continue
            now = time.time()
            small = downscale(frame, settings.vision.process_width)
            scene = await asyncio.to_thread(analyze_frame, small, settings.vision, now)
            motion = meter.update(small)
            await ws.send_json(
                {
                    "type": "update",
                    "scene": scene.to_json(),
                    "zones": zones_json(),
                    "tracker": {"motion": round(motion, 2)},
                    "frameMs": round((time.perf_counter() - t0) * 1000, 1),
                }
            )
    except WebSocketDisconnect:
        pass
