# TeachBack — Progress Log

## Plan (short)

| # | Milestone | Status |
|---|-----------|--------|
| 1 | Scaffold FastAPI + React/Vite, one-command startup, webcam → WebSocket → response | in progress |
| 2 | HSV object detection, zones, stacking, SceneState JSON, synthetic-image tests | todo |
| 3 | Stable-state filter, Teach mode, 4-step timeline | todo |
| 4 | Practice mode (correct / skipped / out-of-order / wrong object / wrong zone), unit tests | todo |
| 5 | UI polish, reset/recovery, repeatable demo | todo |
| 6 | Gemini step naming + ElevenLabs voice, both with fallbacks | todo |
| 7 | Hardening, clean-start test, README / DEMO_SCRIPT / ARCHITECTURE / DEVPOST | todo |

Key decisions:
- Python 3.12 venv (`backend/.venv`) — OpenCV wheels are reliable there; system default is 3.14.
- A learned step is a **state transition** (per-object `zone` + `stackedOn` before/after), not an image caption.
- Pass/fail is fully deterministic in `backend/app/engine.py`. Gemini only writes nicer step names.
- Frame backpressure: the browser keeps at most one frame in flight, capped at ~5 fps, so stale frames are never queued.

## Log

(entries appended per milestone)

### Milestone 1 — scaffold (done)
- Backend: FastAPI app `backend/app/main.py`, `/api/health`, `/ws` accepts binary JPEG frames and decodes with OpenCV.
- Frontend: Vite + React 19 + TS 7. `useCamera` (getUserMedia), `useFrameStream` (≤5 fps, one frame in flight, auto-reconnect).
- One-command startup: `npm run setup` once, then `npm run dev` (concurrently runs uvicorn :8000 + Vite :5173 with /api and /ws proxied).
- Verified: `curl localhost:5173/api/health` → `{"ok":true}`; Python ws probe through the Vite proxy returned `{"type":"update","frame":{"w":640,"h":480,...}}`; `tsc` clean.
- Note: the IDE browser pane blocks camera permission, so the real webcam must be checked in Chrome/Edge. A synthetic frame source is being added for automated end-to-end checks.

### Milestone 2 — vision (done)
- `backend/app/vision.py`: HSV segmentation (largest blob per configured color), center → zone rectangle,
  stacking from bbox overlap (overhead) or bottom-edge-on-top-edge contact (angled camera), stacked objects inherit
  the base's zone. `MotionMeter` = % of pixels changed between frames (a mean diff was too diluted by small hands).
- All thresholds in `backend/app/config.py`.
- Frontend: `Overlay.tsx` draws zones + labeled boxes; `Simulator.tsx` is a draggable synthetic tabletop whose canvas
  goes through the identical JPEG → WS → OpenCV path (used for automated E2E checks; also a camera-failure fallback, `?sim`).
- Verified: `npm test` → 7 passed (synthetic JPEG-roundtripped frames: 4-color zones, off-zone, overhead stack,
  angled stack, adjacent-not-stacked, skin/speck rejection, motion meter). In the browser pane (sim source) dragging red
  to B and yellow onto red produced `yellow on red · B`; frame processing ~8–9 ms.
- Gotcha: writing files with `cat >` while Vite runs on Windows can cache an empty module; use atomic writes.
