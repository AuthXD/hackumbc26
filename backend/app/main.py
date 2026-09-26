from __future__ import annotations

import time

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI(title="TeachBack")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            msg = await ws.receive()
            if msg.get("bytes"):
                t0 = time.perf_counter()
                frame = cv2.imdecode(np.frombuffer(msg["bytes"], np.uint8), cv2.IMREAD_COLOR)
                h, w = frame.shape[:2] if frame is not None else (0, 0)
                await ws.send_json(
                    {"type": "update", "frame": {"w": w, "h": h, "ms": (time.perf_counter() - t0) * 1000}}
                )
            elif msg.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
