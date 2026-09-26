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

### Milestones 3 + 4 — stable states, Teach, Practice (done)
- `stability.py`: commits an arrangement only when all tracked objects are visible, motion < threshold, unchanged
  ≥700 ms and ≥3 frames, and different from the last commit. Hands → `moving`; hidden object → `occluded`.
- `engine.py`: `TeachRecorder` (initial layout → 4 `StepDelta`s, refines initial if an object was hidden, explicit
  `undo_last`) and `PracticeEngine` (setup check → per-step postcondition check; errors: `skipped_step`,
  `out_of_order`, `wrong_object`, `wrong_placement`, `extra_change`; never advances after an error; undo → back on track).
- `describe.py`: deterministic wording ("Move the red object from Zone A to Zone B.", stacking, unstacking, carrying).
- `session.py`: modes, keyframes (served at `/api/keyframes/*.jpg`), local JSON persistence in `backend/data/`.
- `vision.suppress_static_stacks`: an object that hasn't moved since the last commit can't *become* stacked
  (fixes a new tower "stacking" the stationary object it touches — found in the browser run).
- Design change found by randomized tests: returning an object to where it was is a legitimate step, so teacher
  undo is an explicit button, not inferred.
- Verified: `npm test` → 59 passed (engine: correct run, skipped, out-of-order, wrong object, wrong zone,
  duplicate, occlusion, undo-then-continue, strict no-advance, reset + new procedure, 25 random judge orders;
  session E2E with real JPEG frames and a simulated hand). Browser (simulator): taught red→B, blue→C, yellow on blue,
  green→B; practice caught "Skipped step 2" with fix hint, undo → "Back on track", finished → "Procedure complete".
- Perf fix: `canvas.toBlob` measured 500–1000 ms in Chromium → switched to sync `toDataURL`; steady 5.0 fps.
- Ops: uvicorn `--reload` hangs on Windows with open websockets → `npm run dev` runs without reload; `npm run stop` frees ports.

### Milestone 5 — polish, recovery, calibration (done)
- One-screen layout at 1440×900 (camera height budgeted by viewport); big status card with Expected / Observed / Fix;
  timeline with before→after keyframes; expected target zone highlighted during practice; presenter keys T/F/U/P/R/M.
- Click-to-calibrate colors (header → "Calibrate colors" → click each object); stored in `backend/data/calibration.json`;
  "Reset colors" restores defaults. Camera-failure panel offers the simulator.
- Recovery: Restart Practice, Reset, explicit Undo last step while teaching; saved procedure reloads on backend restart.
- Safety: tests now use a temp `TEACHBACK_DATA_DIR` (an earlier smoke test had wiped the real saved procedure).
- Verified: `npm test` → 63 passed (adds app smoke test via TestClient + calibration tests); browser: calibration
  clicks sampled red H0 S207 V212 / yellow H26 S213 V242 and detection stayed 99–100%; reset colors OK.

### Milestone 6 — Gemini + ElevenLabs with fallbacks (done, keys not yet provided)
- `integrations.py`: `GeminiDescriber` calls `models/{GEMINI_MODEL}:generateContent` (default `gemini-3.5-flash`;
  docs checked 2026-09: 2.5 Flash is restricted for new projects) with the StepDelta JSON + before/after keyframes,
  `responseMimeType=application/json` + `responseSchema {title, instruction}`. Response is validated (strict JSON,
  length limits, must mention every handled object's color) and cached per delta. Runs in the background after a
  step is learned; stale results (step undone meanwhile) are discarded. Never used for pass/fail.
- `ElevenLabsVoice` + `POST /api/speak` proxy (`eleven_flash_v2_5`), LRU-cached; returns 503 when absent/failing and
  the browser falls back to `speechSynthesis` per utterance. Only step completion, errors, and mode changes are spoken.
- Verified: `npm test` → 68 passed (mock-transport tests: success + cache, HTTP 500, garbage JSON, empty body,
  network error, missing key, hallucination guard; ElevenLabs success/cache/401/missing key; `/api/speak` → 503).
  Live: `/api/health` → gemini/elevenlabs false, `/api/speak` → 503.
- `npm run demo:check` (live server, real WS + OpenCV, simulated hand): **3/3 consecutive runs passed** —
  skipped_step, wrong_object, wrong_placement, each undone/fixed and completed.
