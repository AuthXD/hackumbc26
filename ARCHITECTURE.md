# TeachBack architecture

## Frame path

```
camera frame
  → phone or laptop ownership (a valid JPEG earns it; a stale frame is dropped)
  → mat tracking and validation (simulator bypasses this)
  → canonical perspective warp
  → outer 10% sticker band masked
  → color detector, or a manual semantic scan
  → zones in that same coordinate system
  → procedure or Setup Check evaluation
  → fail-closed verdict
```

A frame that fails mat validation does not update the scene. Tracking loss, excessive motion, a stale transform,
or a camera change clears the current scene and marks an old Setup Check result stale. An in-flight semantic scan
is invalidated, so a late result cannot grade the new view.

## Phone camera ownership

`GET /api/phone-link` returns either the configured trusted `TEACHBACK_PHONE_URL` or a discovered LAN fallback,
with an explicit secure flag. `?phone=1` renders a rear-camera-only page.

Ownership is in `sources.py`. Connecting a phone sets **connected** and does not take the camera. The first valid
JPEG sets **streaming** and that page becomes the owner. A camera error reported by the page is **error** and does
not count as streaming. The hub forwards the owner's JPEG to viewer tabs and sends every tab its own `active`
flag. Inactive laptop tabs stop their local frame pumps. One frame is in flight; there is no frame queue. If the
phone stops sending or disconnects, the laptop may send again. One async lock serializes JSON and binary sends.

## Mat

Calibration stores four clicks, in order: top-left purple creature, top-right frog, bottom-right potion bottle,
bottom-left SteelSeries logo. `quad_problem` rejects a bad order or a quadrilateral that is too small, crossed, or
too skewed. A valid quad is warped to a canonical image whose long side is `canonical_long_side` (960). A portrait
phone photo is rotated counterclockwise, so the canonical image is landscape: raw TL, TR, BR, and BL land on
canonical bottom-left, top-left, top-right, and bottom-right. Zone A is the physical top third and sits on the
left; Zone C is the physical bottom third and sits on the right. The short edge is not stretched. The outer
`band_fraction` (0.10) is painted out before detection. A saved version-1 portrait calibration is rewritten to
this layout. A saved calibration that is already wider than tall, or a landscape file whose height is not the
short side, is rejected and the UI asks to recalibrate.

The tracker (pyramidal Lucas-Kanade, RANSAC homography, ORB re-acquisition) fails closed. `trustworthy` is false
while the view is unsteady, lost, or waiting to be recalibrated. The UI asks for `/api/mat-view.jpg` only while
that view is current, and drops it about two seconds after tracking stops producing a new one.

The simulator sets mat bypass. Uncalibrated camera mode still runs detection on the raw frame and shows raw zones.

## Semantic worker

`LocateAnythingDetector` talks to one persistent worker (`locate_worker.py` → `benchmarks/locate_anything/worker.py`
under WSL/CUDA when the command is not injected). Selecting Semantic Objects calls `start_preload`. The session
returns immediately. A later snapshot is broadcast when the load thread finishes.

States are `unloaded`, `loading`, `ready`, and `error`. Ready is set only after the worker's `ready` message and a
successful `{"type":"probe"}` reply. Concurrent `preload` calls share one process. `predict` reuses that process.
A timeout or crash sets `error`, terminates the process, and stores a fixed public sentence. Stack traces and
config values stay out of snapshots.

`LatestScan` runs one inference and holds at most one replacement request. A newer scan, motion, camera shift, or
mat loss bumps the version so the in-flight result is discarded. Scans are refused until `workerState` is `ready`.

The frame written for inference is `prepare_semantic_frame`, limited to `Settings.semantic_max_dim` (default 448,
validated in `config.py`). When the mat is tracking, that frame is the masked canonical image. Otherwise it is the
camera frame. The size sweep that chose 448 is `benchmarks/locate_anything/evidence/size-sweep.md`.

## What clears a previous verdict

- Mat loss, an untrusted track, or a camera / coordinate-system change
- Motion or a camera shift during a scan
- A failed or ambiguous scan (procedure or Setup Check)
- Switching detector or workspace, resetting, or calibrating the mat
- A table move after a Setup Check (`resultStale`)

Color practice verdicts also clear when the arrangement is no longer the committed stable state. Hands moving or
an occluded object are waiting states, not errors, and they do not commit.

Color mode after the mat step (or with the simulator, which skips the mat). Semantic scans replace the HSV
box with one manual LocateAnything call. Speech is the browser when `/api/speak` returns 503.

```
 Browser (React + Vite)                         Backend (FastAPI, Python 3.12)
┌──────────────────────────┐   JPEG frames    ┌──────────────────────────────────────────────┐
│ getUserMedia webcam      │ ───────────────▶ │ vision.py   HSV masks → one blob per color    │
│  or Simulator <canvas>   │  (≤5 fps, one in │             center → Zone A/B/C               │
│                          │   flight, no     │             bbox overlap/contact → stackedOn  │
│ Overlay: zones + labels  │   backlog)       │             static-stack filter, motion %     │
│ Status card (Exp/Obs/Fix)│                  │                       │ SceneState            │
│ 4-step timeline          │ ◀─────────────── │ stability.py  visible? still? unchanged       │
│ Speech: ElevenLabs proxy │  JSON update per │               ≥700 ms? new? → commit          │
│   → speechSynthesis      │  frame + events  │                       │ stable SceneState     │
└──────────────────────────┘                  │ engine.py   TeachRecorder → Procedure         │
        ▲  /api/speak (mp3 or 503)            │             PracticeEngine → PracticeState    │
        └──────────────────────────────────── │ describe.py deterministic wording             │
                                              │ integrations.py  Gemini (wording only),       │
                                              │                  ElevenLabs (voice only)      │
                                              │ session.py  modes, keyframes, JSON persistence│
                                              └──────────────────────────────────────────────┘
```

## Data model (backend/app/models.py)

- **SceneObject**: `id`/`color`, `center`, `bbox`, `zone` (A/B/C or none), `visible`, `stackedOn`, `confidence`.
- **Placement**: the part the procedure cares about, `(zone, stackedOn)`. An *arrangement* maps each tracked
  object to its placement.
- **SceneState**: objects, `capturedAt`, `stableSince`.
- **StepDelta**: `changedIds`, `before`, `after`. `after` doubles as the step's **postconditions**.
- **LearnedStep**: index, before/after states, delta, before/after keyframe URLs, deterministic description,
  optional AI description.
- **Procedure**: initial state, tracked object ids, ordered steps.
- **PracticeState**: `expectedStepIndex`, `status` (setup / waiting / step_complete / error / complete),
  `errorType`, expected / observed / fix text.

## Setup Check (backend/app/setups.py)

A second product mode, separate from Teach/Practice. `Session.workspace` is `procedure` or `setup`. In `setup`,
procedure commands are refused and the detector stays semantic.

- **SetupObject** `{label, zone}` and **SavedSetup** `{id (slug of name), name, objects[1–6], createdAt}`. Both are
  validated pydantic types with unique case-insensitive labels, and the id must match the name.
- **SetupCheckResult** `{status: complete | needs_attention, correct, missing, unexpected, misplaced, checkedAt}`.
  The model itself rejects `complete` whenever any finding is present.
- **SetupRepository** protocol (`list` / `get` / `save` / `refresh` / `status` / `errors`). `list`/`get` serve a
  validated in-memory cache (they run on every snapshot, ~5x/s); only `refresh` and `save` touch storage.
  `status()` returns **StorageStatus** `{provider: local | tiger, state: ready | error, message}`, surfaced in
  snapshots and `/api/health`.
  - `JsonSetupRepository`: one file per setup in `DATA_DIR/setups/`, atomic writes, malformed files skipped and
    reported.
  - `TigerSetupRepository` (`tiger.py`, psycopg 3): Tiger Cloud is plain PostgreSQL. TLS is enforced (sslmode
    upgraded to `require` and `ssl_in_use` checked), with `connect_timeout` and a per-transaction
    `statement_timeout` via `set_config(..., true)`. SQL is parameterized. `save` is
    `INSERT ... ON CONFLICT (id) DO UPDATE ... RETURNING`, and the returned row is validated before caching. Every
    row read passes `SavedSetup` validation; invalid rows are skipped into `errors`. Failures expose only the
    exception class, never the URL, host, or user.
  - `create_setup_repository()` chooses Tiger when `TIGER_DATABASE_URL` is set, else JSON. A configured but
    unreachable Tiger yields a Tiger repository in `error`. It never falls back to local files, and capture is
    refused. The session holds no SQL. It saves outside `Session.lock` so frames keep flowing during the
    (bounded) round trip.
- **Schema** `backend/sql/001_tiger_setups.sql`: a normal table (not a hypertable; rows are current records, not
  events) `teachback_setups(id TEXT PK, name, objects JSONB, created_at, updated_at DEFAULT NOW())`. CHECK
  constraints cover the id slug, name length, and a 1–6 element objects array. It is applied only by the explicit
  `npm run tiger:check`, which also runs a rolled-back insert/upsert/read/constraint probe.
- **History** (`history.py`, `tiger.py`, `sql/002_tiger_setup_check_history.sql`). Every accepted verdict
  becomes one immutable **SetupCheckEvent** `{eventId, checkedAt, setupId, setupName (snapshot), status,
  correct, missing, unexpected, misplaced}`, created only in `_finish_setup_check`. A scan result is delivered
  once, so repeated frames or snapshots cannot add events. **CheckHistoryRepository** (`record` / `recent` /
  `summary` / `refresh` / `status` / `close`) is separate from SetupRepository.
  - `TigerCheckHistoryRepository`: `teachback_setup_checks` is a TigerData hypertable
    (`WITH (tsdb.hypertable, tsdb.partition_column='checked_at')`) with `PRIMARY KEY (event_id, checked_at)`
    (unique keys must include the partition column), a status CHECK, JSON-array findings, and an index on
    `(setup_id, checked_at DESC)`. Inserts are `ON CONFLICT (event_id, checked_at) DO NOTHING`, so retries are
    idempotent; there is no UPDATE or DELETE. The readiness summary uses `time_bucket('1 hour', checked_at)`
    over 24 h plus all-time totals. Rows become `SetupCheckEvent` / `ReadinessBucket` / `ReadinessSummary`,
    and invalid ones are skipped into `errors`. The startup check requires the table to be a hypertable
    according to `timescaledb_information.hypertables`.
  - `DisabledCheckHistory` (no Tiger): state `disabled`. Checks still work, and nothing claims to be recorded.
  - **HistoryWriter**: one daemon thread and one bounded `queue.Queue` (32) for all history I/O. `submit()` and
    `request_refresh()` never block (they run under `Session.lock`). A full queue drops the job, counts it,
    and logs it. Each `event_id` is accepted at most once, with per-event state pending → saved / failed /
    dropped / disabled. A successful write triggers a refresh of that setup's caches. `close()` stops intake,
    drains accepted jobs, and joins within `history_close_timeout` (5 s). Snapshots and `/api/health` read
    caches only, and the writer notifies the event loop to broadcast when history changes.
- **Capture** requires an accepted *strict* semantic scan: exactly one box per configured description.
- **Check** submits a scan with `purpose="setup_check"`. Its vocabulary is the setup's labels plus the other
  configured descriptions (max 6). Only that purpose uses `allow_missing`, because absence is the finding; duplicate,
  unknown, invalid, and overlapping boxes are still rejected. `check_setup()` compares labels and zones.
  A worker error or ambiguous scan yields no result, motion discards an in-flight check, and a later move marks the
  last result stale. No LLM is involved.

## Procedure Library (backend/app/library.py)

Named, saved procedures. The *active* procedure is still `Session.procedure`, mirrored to `procedure.json`; the
library is a separate store and never replaces that file's role.

- **SavedProcedure** `{version: 1, id (slug of name), name 1–60, summary ≤200, tags ≤3, procedure, createdAt,
  updatedAt, aiGeneratedMetadata}`. Validation re-checks the embedded Procedure: 1–20 steps, indexes 0..n-1,
  unique tracked ids (≤12), one detector kind. `card()` is the list view sent to the page.
- **ProcedureRepository** protocol (`list` / `get` / `save` / `refresh` / `status` / `errors`), the same shape as
  SetupRepository: `list`/`get` serve a validated cache, only `refresh` and `save` touch storage, and `save` is an
  explicit upsert that keeps `createdAt`.
  - `JsonProcedureRepository`: one atomic file per procedure in `DATA_DIR/procedures/`; malformed or renamed
    files are skipped into `errors`.
  - `TigerProcedureRepository` (`tiger.py`): `teachback_procedures` (migration 003) is a normal table with
    searchable columns (name, summary, `tags TEXT[]`, detector_kind, step_count, object_count) plus the
    validated Procedure as JSONB, with CHECK constraints mirroring the model. Every row is re-validated, and a row
    whose searchable columns disagree with its payload is rejected. Configured-but-unreachable Tiger is an
    `error` state that refuses saves; there is no local fallback. Keyframe images are not stored.
- **Session**: `_complete_teaching` starts an unsaved draft with a deterministic `fallback_metadata` name.
  `save_procedure` runs its I/O outside `Session.lock` and never mutates the procedure. `load_procedure` works
  only in Procedure mode while idle: it deep-copies the entry, rewrites `procedure.json`, restores any keyframes
  still on disk, resets the tracker, and restores the detector (semantic needs the beta; if the model is not
  ready, the procedure stays loaded and the notice says so). Setups, history, mat calibration, and camera
  ownership are untouched. `reset` clears the active procedure but keeps the library and the keyframe files it
  references. `library.revision` is bumped when a save/load/refresh finishes, so the page knows its reply came.

## Key decisions

1. **Steps are state transitions, not captions.** Teaching records the before and after arrangement of each settled
   change. Practice compares arrangements, so a judge's arbitrary order is learned exactly as performed.
2. **Commit only settled states.** A state commits once all tracked objects are visible, pixel motion is below the
   threshold, and the arrangement has been unchanged for at least 700 ms over at least 3 frames. Hands in motion
   read as `moving`, and a covered object reads as `occluded`. Neither can become a step or an error.
3. **Deterministic judging.** Given the expected step *k* and the base arrangement before it:
   - every expected object meets its postcondition and nothing else moved → **advance**;
   - unexpected changes match a later step's postcondition → **skipped_step** (it was step *k+1*) or
     **out_of_order** (a step further ahead);
   - the expected object moved to the wrong place → **wrong_placement**;
   - a different object moved → **wrong_object**;
   - the step was done but something else also moved → **extra_change**.

   An error never advances the sequence. Returning to the base arrangement clears it ("back on track"), and reaching
   the correct postconditions directly also passes. A stacked object only needs to be on the right base, because its
   zone follows the base.
4. **Causal stacking filter.** In a single 2D frame, "on top of" and "touching from behind" look the same. An object
   that hasn't moved since the last committed state therefore cannot *become* stacked.
5. **AI is advisory.** Gemini gets the StepDelta JSON and both keyframes and must return strict JSON
   `{title, instruction}`. The answer is validated (it must mention every handled object) and cached per delta. It
   only changes the wording in the timeline. ElevenLabs only voices text the engine produced. Both fall back silently.
6. **Backpressure by design.** The browser sends the next frame only after the previous result arrives. JPEG
   encoding uses synchronous `toDataURL` because Chromium's async `toBlob` was measured at 500–1000 ms.

## Simulator

Simulator frames are JPEGs of a canvas. They use the color detector and skip mat tracking, canonical warp, and
semantic scans. Semantic mode disables the Simulator control so a synthetic table cannot be graded as real objects.

## Tests

- `test_engine.py`: teach and practice logic on synthetic arrangements, including 25 randomized judge procedures and
  swapped-step detection.
- `test_vision.py`: OpenCV on synthetic JPEG-roundtripped frames (zones, stacking, skin rejection, motion,
  calibration).
- `test_session.py`: end-to-end through real frames with a simulated hand.
- `test_integrations.py`: Gemini and ElevenLabs with mock transports.
- `test_app.py`: app smoke test.
- `test_setup.py`: Setup Check capture/persistence, all verdict types, malformed files, scan failures, and procedure
  isolation.
- `test_tiger.py` (+ `fake_tiger.py`, an offline psycopg fake with transactions and the schema's constraints):
  selection, idempotent migration, parameterized upsert, row validation, outage without fallback, refused capture,
  credential redaction, cache use, TLS enforcement, the non-blocking save, and the `tiger:check` flow.
- `test_history.py`: event model, migration order and idempotency, hypertable and composite key, append-only
  parameterized insert, one event per check, non-blocking writes, visible overflow, failed writes never shown as
  saved, secret redaction, cache-only reads, `time_bucket` validation, local disabled mode, and a bounded drain.
- `demo_check.py`: three full demos against the live server.
