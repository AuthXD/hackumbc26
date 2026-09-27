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

### Semantic Objects constrained beta (2026-09-26)
- The full OnePlus 12 / black-mat benchmark covered 12 descriptions across three layouts: separated 10/12,
  adjacent 7/12, overlap/clutter 4/12; 21/36 overall (58.3%). Runtime passed: 11.015 s cold startup, 1.834 s median,
  4971 MiB sampled GPU peak, 10/10 requests. The broad >=80% arbitrary-object gate failed.
- Added an opt-in `TEACHBACK_SEMANTIC_BETA=1` manual-scan mode for four verified objects: blue water bottle, brown
  wallet, green smartwatch, and blue smartphone. Color mode remains unchanged and is the default.
- Semantic results require exactly one valid box per requested description and reject missing, duplicate,
  unexpected, or heavily overlapping objects. Confidence remains unavailable rather than fabricated; stacking is
  unsupported. One active inference plus one replaceable pending request prevents stale results from grading.
- Saved procedures preserve their detector identity. Switching object descriptions or detectors is locked during
  Teach/Practice; Pause preserves the procedure and enables an explicit Color fallback.
- Verification: 91 tests passed; TypeScript typecheck and production build passed; original live demo check passed
  3/3. A real WebSocket scan of the separated OnePlus photo found all four recommended objects and mapped zones.
  Cold app scan was 21.5 s including model load; warm rescan was 2.078 s. Color fallback preserved the procedure.
- Next product unit: a separate Setup mode for inventory/order checks, followed by Tiger Data persistence and then
  Gemini retrieval. Do not put those changes in the same commit as detector integration.

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

### LocateAnything bounded spike (2026-09-26)
- Added isolated `benchmarks/locate_anything` worker and evaluator. Reuses the user's
  installed Ubuntu CUDA C++ port and Q6_K model; no new model download or app dependency
  changes. Linked existing archives into a separate shared library without changing
  the Ubuntu source/build. Model/runtime hashes saved in `evidence/provenance.json`.
- Persistent JSONL worker returns normalized labels and xyxy boxes. Confidence is
  explicitly null because the installed C API exposes none. Startup and inference
  deadlines, native error-log checks and process cleanup are separate from the app.
- `evaluate.py` records process-cold startup, per-request inference/round trip, 100ms
  device-wide GPU memory samples, raw predictions, input hashes and annotated PNGs.
  Gate requires independent real-tabletop ground truth, >=10 categories across >=3
  distinct images, >=80% critical-instance recall at exact label + IoU >=0.5,
  median <3s, and >=10 requests with no crash/OOM/restart. Smoke evidence cannot pass.
- Final command: `backend/.venv/Scripts/python.exe benchmarks/locate_anything/evaluate.py
  --manifest benchmarks/locate_anything/smoke.json --output benchmarks/locate_anything/results/final-smoke-640
  --requests 10 --provenance benchmarks/locate_anything/evidence/provenance.json`.
  Result: startup 9.967s, median 1.336s, peak 4957 MiB, ten requests completed, CUDA0
  confirmed, no native failures. **Not adopted**: only two smoke categories/two images.
- Prior smoke runs: 15.287s/1.378s/4972 MiB and 11.049s/1.401s/4957 MiB,
  each ten requests. Three apples + foreground beaker localized; manually inspected
  overlays. This is not accuracy evidence for the proposed demo objects.
- Real timeout probe: same command with `--output .../timeout-probe --requests 2
  --timeout 0.05` correctly rejected request 1 and exited; GPU usage returned to 540 MiB.
- Verification: `backend/.venv/Scripts/python.exe -W error::ResourceWarning -m unittest
  discover -s benchmarks/locate_anything -p 'test_*.py' -v`: 13 passed, including
  gate boundaries, duplicate matching, missing data, crashes and timeouts.
  `npm test`: 68 passed; `npm run typecheck`, `npm run build`, compileall and diff check passed.
- Live app: isolated Vite :5174/API :8011 browser rehearsal taught red A-to-B,
  finished early, restored start and reached Procedure complete; ~5fps, no console errors.
  Post-spike `TEACHBACK_DEMO_URL=ws://127.0.0.1:8011/ws npm run demo:check` passed 3/3
  while the final GPU benchmark ran. Color detector, simulator and production path unchanged.
- `LOCATE_ANYTHING_EVAL.md` contains the decision, detector boundary, evidence, limits
  and conditional integration plan. `benchmarks/locate_anything/README.md` has commands
  and ground-truth schema; `demo-objects.json` has the user's nine items plus a proposed pen.
- **Pending input:** saved tabletop photos for at least three arrangements and a tenth
  object. User supplied object names, not images. Do not implement the production
  Detector interface/tracking/scheduling until the full adoption gate passes.

### Setup Check mode (2026-09-26)
- New product unit, separate from Teach/Practice: capture an accepted semantic scan as a named expected setup, save
  it locally, select it, and check a new scan against it. Reports complete / missing / unexpected / wrong zone.
- `backend/app/setups.py`: explicit types (`SetupObject`, `SavedSetup`, `MisplacedObject`, `SetupCheckResult`), a
  `SetupRepository` protocol, `JsonSetupRepository` (one atomic JSON file per setup under `DATA_DIR/setups/`,
  malformed files skipped and reported), and a pure `check_setup()`. No Gemini/LLM involvement.
- Detector: `semantic_scene(..., allow_missing=False)`. Only scans submitted with `purpose="setup_check"` allow
  absent labels. Duplicate, unknown, invalid, and overlapping boxes stay ambiguous, and LocateAnything inference is
  unchanged. Procedure scans remain strict.
- Session: `workspace` = procedure | setup. In setup, procedure commands and the Color switch are refused, so
  `procedure.json` is never touched. Failed or ambiguous check scans give no verdict and clear the previous one;
  motion cancels an in-flight check and marks an old result stale.
- UI: Procedure / Setup Check selector, setup name + Capture Setup, saved-setup selector + Check Setup, and result
  lists (Missing / Unexpected / Wrong zone / Correct) plus a Setup view in the status card.
- Verification: see the final report for this unit (npm test, typecheck, build, demo:check, git diff --check).
  An isolated browser run (API :8011 with the beta enabled, temp data dir) confirmed: mode switch, the selector
  listing a saved setup, a malformed file reported, the expected list, the T shortcut ignored in Setup, and
  switching back restoring the procedure controls, with no console errors. Real-camera scans were not possible in
  the IDE browser.
- Next unit: a Tiger Data `SetupRepository` implementing the same protocol (no session changes expected).

### Tiger Data persistence for saved setups (2026-09-26)
- `TigerSetupRepository` (`backend/app/tiger.py`, psycopg 3.2–3.3) implements the existing `SetupRepository`
  protocol, which gained `refresh()` and `status()`. Session contains no SQL; `create_setup_repository()` picks
  Tiger only when `TIGER_DATABASE_URL` is set.
- TLS is enforced (sslmode upgraded to `require`; `pgconn.ssl_in_use` verified), with a 5 s connect timeout and a
  5 s per-transaction statement timeout. All SQL is parameterized. Save is `INSERT ... ON CONFLICT (id) DO UPDATE`
  `... RETURNING`, and the returned row is validated before caching. Reads are cached in memory: the database is
  touched at startup, on capture, and on explicit Retry only.
- Outage: Tiger stays selected in the error state (no local fallback). The app and Procedure mode still start.
  Capture is refused with a sanitized message. The storage state is in snapshots, `/api/health`, and the Setup
  panel (Storage: Local / Storage: Tiger Data / Tiger Data unavailable + Retry connection).
- Capture saves outside `Session.lock`; a test proves frames are processed while a save is blocked.
- Migration `backend/sql/001_tiger_setups.sql` (plain table + CHECK constraints) and `npm run tiger:check`
  (migrate twice, rolled-back insert/upsert/read/constraint probe, sanitized output).
- Verification: offline tests use `tests/fake_tiger.py`. Separately, the migration, repository, CHECK
  constraints, statement timeout, and the full `tiger_check` flow were run against a real local PostgreSQL 16
  (scratch `pgserver`, not a repo dependency). The non-TLS local server was correctly refused by
  `tiger:check`. An isolated app with an unreachable `TIGER_DATABASE_URL` started normally and showed
  "Tiger Data unavailable"; no credential appeared in health, logs, or the page.
- **Live Tiger Cloud verification is pending**: no `TIGER_DATABASE_URL` exists on this machine.

### Tiger Data setup-check history and readiness analytics (2026-09-26)
- `SetupCheckEvent` (immutable, one `event_id` per accepted verdict; status must match its findings),
  `ReadinessSummary`/`ReadinessBucket`, a separate `CheckHistoryRepository` protocol, `DisabledCheckHistory` for
  local mode, and one bounded `HistoryWriter` thread (queue 32, non-blocking submit, visible overflow, per-event
  state, 5 s drain on close). Session creates the event only in `_finish_setup_check`; no DB call under its lock.
- Migration `002_tiger_setup_check_history.sql`: `teachback_setup_checks` hypertable, `PRIMARY KEY (event_id,
  checked_at)`, status/slug/name/JSON-array CHECKs, `(setup_id, checked_at DESC)` index. Inserts use
  `ON CONFLICT (event_id, checked_at) DO NOTHING`; there is no UPDATE or DELETE. Summaries use
  `time_bucket('1 hour', checked_at)` over 24 h plus all-time totals. `tiger:check` applies every numbered
  migration in sorted order, twice.
- **Compatibility decision (live service):** PostgreSQL 18.6, TimescaleDB 2.30.1. The modern
  `CREATE TABLE ... WITH (tsdb.hypertable, tsdb.partition_column = 'checked_at')` form is accepted, including with
  `IF NOT EXISTS` on re-runs, so no `create_hypertable()` fallback is needed. On this version that form also
  enables the columnstore and auto-creates a `policy_compression` job (1-day schedule) plus a default
  `checked_at` index, all verified via `timescaledb_information`. This is kept as-is: the history is append-only
  and inserts target current (uncompressed) chunks. Default chunk interval: 7 days.
- Live verification (sanitized): 002 was first validated inside a rolled-back transaction (applied twice,
  hypertable metadata, insert, duplicate ignored, `time_bucket`, status CHECK, and no table after rollback).
  `npm run tiger:check` then passed twice (TLS; 001+002 applied and re-applied; setups probe; hypertable
  partitioned on `checked_at`; two events, duplicate ignored, recent, `time_bucket` summary, rows validated;
  0 rows and 0 chunks left). The app's real `HistoryWriter` + `TigerCheckHistoryRepository` ran against live
  Tiger inside one rolled-back transaction: 2 saved, re-submit not duplicated, summary 2 checks / 50% / one
  hourly bucket, recent validated, 0 rows left. An isolated app on :8011 reported storage and history
  `tiger/ready`, the panel showed "History: Tiger Data", and 0 credential fragments appeared in health or logs.
- Not yet exercised live: a full camera-driven Setup Check writing a *committed* history row (needs the
  webcam + semantic model), and the readiness/recent lists rendered with real data in the browser.
