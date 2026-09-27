# TeachBack architecture

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
