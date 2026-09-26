# TeachBack — Progress Log

## Plan (short)

| # | Milestone | Status |
|---|-----------|--------|
| 1 | Scaffold FastAPI + React/Vite, one-command startup, webcam → WebSocket → response | done |
| 2 | HSV object detection, zones, stacking, SceneState JSON, synthetic-image tests | done |
| 3 | Stable-state filter, Teach mode, 4-step timeline | done |
| 4 | Practice mode (correct / skipped / out-of-order / wrong object / wrong zone), unit tests | done |
| 5 | UI polish, reset/recovery, repeatable demo | done |
| 6 | Gemini step naming + ElevenLabs voice, both with fallbacks | done |
| 7 | Hardening, clean-start test, README / DEMO_SCRIPT / ARCHITECTURE / DEVPOST | done |

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

### Milestone 7 — hardening + docs (done)
- Clean-start test: fresh `git clone` → `npm install` → `npm run setup` → `npm test` (68 passed) →
  `npm run typecheck` → `npm run build` (237 kB JS) → `npm run dev` → `npm run demo:check` (3/3 passed), no API keys.
- Bug found by the clean start: setup failed when the path contains a space ("C:UsersKomal Tummala...") because
  absolute executables were spawned through a shell — fixed (shell only for bare `py`/`npm`).
- Fixed a StrictMode race that flashed "Offline" on load. Camera-denied panel verified (offers the simulator).
- Docs: README (Windows setup, physical setup, demo flow, calibration, troubleshooting, limitations),
  ARCHITECTURE.md, DEMO_SCRIPT.md (60 s), DEVPOST.md (incl. track notes).

## MVP verification checklist
- [x] New procedure taught without code edits (browser sim + 3 different procedures in demo:check + 25 random orders in tests)
- [x] Timeline shows four meaningful steps with before/after keyframes
- [x] Correct attempt completes
- [x] Error types caught: skipped_step, out_of_order, wrong_object, wrong_placement, extra_change
- [x] Correct an error and continue (undo → "Back on track"; wrong zone fixed directly)
- [x] Reset works (and a different procedure can then be taught)
- [x] Full demo 3× consecutively (`npm run demo:check`, live server)
- [x] Starts from documented commands (clean clone)
- [x] Missing API keys don't break the core demo
- [ ] **Real laptop webcam** — not verifiable from the dev environment (its browser blocks cameras); run once in
      Chrome with the real objects and calibrate colors.

## Current commands
`npm run setup` · `npm run dev` · `npm test` · `npm run demo:check` · `npm run typecheck` · `npm run stop`

## Next tasks
1. Real-webcam rehearsal with the physical objects under venue lighting; tune `motion_threshold` / calibrate.
2. Add `GEMINI_API_KEY` / `ELEVENLABS_API_KEY` to `.env` and confirm AI step titles + voice live.
3. Optional: HTTPS dev server for the iPhone camera path.

### Takeover baseline (2026-09-26)
- Inspected README, architecture, demo script, package scripts, history and camera/stream,
  detector, SceneState, stability, teaching/practice, persistence and both integrations.
- Started from clean `ca08776`. Color detector and simulator remain unchanged.
- `npm test`: 68 passed; pytest cache write warning only. `npm run typecheck` and
  `npm run build`: passed. This shell needs `$env:NPM_CONFIG_PREFIX='C:\Program Files\nodejs'`
  to avoid the broken roaming npm launcher; no global npm configuration was changed.
- Added `TEACHBACK_DEMO_URL` override to the demo checker, retaining its default URL.
  Port 8000 was already occupied. Ran a separate server on 8011 with
  `TEACHBACK_DATA_DIR=C:\AuthXD\hackumbc26\backend\data\baseline-spike`, then
  `$env:TEACHBACK_DEMO_URL='ws://127.0.0.1:8011/ws'; npm run demo:check`: all 3 runs passed,
  including error detection, no premature advance, recovery and completion.
- Detector boundary: `Session.process_frame` calls `vision.analyze_frame`, which wraps
  `detect_objects` into `SceneState`; detection also infers stacking and zones. Motion and
  arrangement stability follow detection. Object IDs currently equal color names.
- LocateAnything spike begins separately. RTX 4060 Laptop GPU, 8188 MiB VRAM. User already
  has Ubuntu WSL, CUDA-enabled locate-anything.cpp at `77376ab332de918220f7a7e391542eefb5407c9f`
  and `/home/authxd/models/locate-anything-q6_k.gguf`. Real demo-object photos still needed.
