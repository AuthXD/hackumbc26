# TeachBack

**Show a procedure once. TeachBack coaches the next person through it.**

TeachBack watches one demonstration of a short tabletop procedure through a webcam and learns it as a sequence of
state changes: which object ended up in which zone or on top of what. When the next person practices, it checks every
settled state against that sequence. It shows and speaks a correction when they skip a step, do steps out of order,
move the wrong object, or put the right object in the wrong place. After they fix the mistake, it coaches them on to
the end.

Nothing is hardcoded to a particular procedure. A judge can invent any four-step order on the spot.

---

## Quick start (Windows)

Prerequisites: **Node.js 20+** and **Python 3.12** (3.11 also works). Python 3.12 is preferred because OpenCV wheels
are reliable there. `npm run setup` picks it automatically through the `py` launcher.

```bash
npm install
```

```bash
npm run setup
```

```bash
npm run dev
```

Then open **http://localhost:5173** in Chrome or Edge and allow camera access.

`npm run setup` creates `backend/.venv`, installs the Python requirements, and installs the frontend packages.
`npm run dev` starts the API on :8000 and the web app on :5173, which proxies `/api` and `/ws`. Stop both with
Ctrl+C, or run `npm run stop` if a previous run left a port busy.

### Use a phone as the camera

Press **Connect phone** on the laptop. TeachBack shows a QR and a phone-only page that requests the rear camera.
The phone does not take the camera until its first valid JPEG arrives. Until then the button reads **Phone
connected — no video yet**. After valid frames it reads **Phone streaming**, and a camera failure reads **Phone
camera error**. The laptop keeps the controls and mirrors the phone's live JPEG frames. A phone that stops sending,
or disconnects, gives the camera back to the laptop. Stale frames are dropped; they never stay in a queue.

Mobile browsers require a trusted HTTPS origin for live camera access. The automatically discovered LAN URL is
useful for checking connectivity, but its `http://` QR cannot open the camera on iPhone or normal Android Chrome.
For a direct phone connection, run a trusted HTTPS tunnel to `http://localhost:5173`, then either paste its URL into
the **Phone URL** field or set `TEACHBACK_PHONE_URL` in `.env` and restart. For example, if `cloudflared` is already
installed:

```bash
cloudflared tunnel --url http://localhost:5173
```

Using a public tunnel sends camera frames through that tunnel provider. For a fully local path, expose the phone as
a Windows webcam with Camo or DroidCam and use the normal **Camera** source instead.

No camera handy? Click **Simulator** in the header, or open http://localhost:5173/?sim. You get a synthetic
tabletop whose pixels go through the color detector. The simulator skips mat tracking. Semantic Objects stays on
the camera path; the Simulator button is disabled while that mode is selected.

### Mat calibration

With a real camera (laptop webcam or phone), press **Calibrate mat** and click the four stickers in order:

1. top-left purple creature
2. top-right frog
3. bottom-right potion bottle
4. bottom-left SteelSeries logo

TeachBack checks that those clicks form a usable quadrilateral, then perspective-warps the picture into a fixed
top-down canonical view. The outer 10% of that view is the sticker band. It is masked out and is not part of
Zones A, B, and C.

Tracking fails closed. If a landmark is lost, the camera moves too much, the transform goes stale, or the camera
source changes, TeachBack drops the current scene and will not give a verdict until tracking is trustworthy
again. The stabilized picture is removed when it is no longer current. The simulator does not use this path.

```bash
npm run mat:check
```

That command is offline. It does not start the server, use the database, or call an API. On this machine it
reads `IMG_4738.jpg` from the default path in `backend/mat_check.py`. Another machine can pass `--image` or set
`TEACHBACK_MAT_CHECK_IMAGE`. The landmark coordinates are the checked-in fixture
`benchmarks/mat/img_4738.landmarks.json`. It writes:

- `benchmarks/mat/results/annotated-source.png`
- `benchmarks/mat/results/canonical.png`
- `benchmarks/mat/results/canonical-band.png`
- `benchmarks/mat/results/mat-check.json`

Those generated images are gitignored. The pytest suite uses a synthetic mat, so CI does not need that photo.

### Optional API keys

Copy `.env.example` to `.env` in the repo root and fill in any keys you have, then restart `npm run dev`.

| Key | What it adds | Without it, or if the call fails |
|-----|--------------|-----------|
| `GEMINI_API_KEY` | Optional wording for a learned step, from the before/after keyframes plus the structured change | Deterministic text, e.g. "Move the red object from Zone A to Zone B." |
| `ELEVENLABS_API_KEY` | Optional spoken corrections and step completions | Browser `speechSynthesis` |

Neither service decides whether a step was correct. That decision is always deterministic. A failed Gemini or
ElevenLabs call, including an HTTP 401 from a rejected key, is treated as unavailable. `/api/speak` returns 503
and the browser speaks with `speechSynthesis`. This repo does not claim that a live Gemini or ElevenLabs call
succeeds with the key currently on disk.

### LocateAnything (WSL and CUDA)

Semantic Objects uses a resident LocateAnything Q6_K worker. The web server does not import the model. On Windows
the worker runs in WSL (`LOCATE_WSL_DISTRO`, default `Ubuntu`) with `LA_DEVICE=CUDA0`. You need:

- the Q6_K GGUF at `LOCATE_MODEL` (default `/home/authxd/models/locate-anything-q6_k.gguf` inside that distro)
- the shared library from `benchmarks/locate_anything/build_shared.py`, or `LOCATE_LIBRARY`

Color mode does not need any of this.

### Optional Semantic Objects beta

The four-color path remains the default. To enable manual recognition of ordinary objects, set
`TEACHBACK_SEMANTIC_BETA=1` in `.env` and restart. The UI then offers **Semantic Objects · Beta**.

Selecting that mode starts the worker in the background. The rest of the app stays usable. The status is plain
text:

- **Model not loaded**
- **Loading model**
- **Model ready**
- **Model error**

Ready means the worker sent its ready message and then answered a probe. Starting the process is not enough.
**Scan Objects** stays disabled until Ready. Choosing Semantic Objects again while a load is in progress reuses
that one worker; it does not start a second one. After Ready, later selections reuse the same process.

If loading fails, the card says **Model error** and offers **Retry model**. Color mode still works. The UI does
not show stack traces or configuration values.

Use 2–6 unique descriptions. The measured demo set is `blue water bottle, brown wallet, green smartwatch, blue
smartphone`. Keep every object separated and fully visible on the black mat. In Teach or Practice, wait for the
table to settle and press **Scan Objects** after the starting layout and after every move. Only the latest scan
is kept. A newer frame or request supersedes an older one. There is no inference queue.

The image sent to the model is the canonical mat when tracking is active, and the camera frame when the mat is
not calibrated. The longest edge is `TEACHBACK_SEMANTIC_MAX_DIM` (default **448**). Values outside 224–1280, or
anything that is not a whole number of pixels, are rejected at startup.

On the three evaluation photos, 448 detected all four requested labels on every image. Warm median latency was
**0.874 s** across **15** warm inferences (5 trials × 3 images). The model API does not return confidence scores.
Cold start on that run was 14.598 s at 448. Cold starts at 512 (12.034 s) and 640 (6.904 s) are records of those
process launches, not evidence that a larger image loads the model faster. CUDA, disk, and OS caches can change
a cold start. 448 was chosen because it kept the detections, not because its cold start was shorter.

```bash
npm run semantic:sizes
```

That sweep needs the WSL worker, the model, and the three photos. Override the photo paths with
`TEACHBACK_SEMANTIC_IMAGE_1`, `TEACHBACK_SEMANTIC_IMAGE_2`, and `TEACHBACK_SEMANTIC_IMAGE_3`. Results are written
to `benchmarks/locate_anything/evidence/size-sweep.json` and `size-sweep.md`.

Stacking is not supported in this mode. If a scan fails, pause the procedure before switching back to Color; the
learned semantic procedure is preserved.

### Procedure Library (named, saved procedures)

After **Finish Teaching**, the status card asks you to **Name and save this procedure** (Practice works straight
away too). In **Procedure Library** below the timeline:

1. The **Procedure name** field starts with a plain default such as *Four-step color-block procedure*. Edit it and
   the optional summary, then press **Save Procedure**. Saving never changes the learned steps.
2. Saving a name that already exists is an explicit replace: the button reads **Replace Saved Procedure** and the
   hint says so. The original creation time is kept.
3. Each card shows the name, summary, detector, object count, step count, and when it was last saved. **Load**
   (only while idle) makes it the active procedure for **Practice**. It also restores the detector: Color, or
   Semantic Objects with the saved object descriptions. If the semantic model is still loading, the procedure
   stays loaded and the notice says to wait for **Model ready**. Without the semantic beta, a semantic procedure
   loads but cannot be practiced.
4. **Reset** clears only the active procedure; saved procedures and their thumbnails stay.

Procedures are stored in Tiger Data when `TIGER_DATABASE_URL` is set (table `teachback_procedures`, migration
003), otherwise as local files under `backend/data/procedures/`. They are separate from the active
`procedure.json`, from saved setups, and from check history. If Tiger is configured but unreachable, the panel
shows **Tiger Data unavailable** and saving is disabled; nothing is written locally instead, and Practice still
works. Keyframe images are not stored in the database, so a procedure loaded on another machine simply shows no
thumbnails.

### Setup Check mode (needs the Semantic Objects beta)

Setup Check is separate from Teach/Practice. It learns what an organized workspace looks like, such as a lab bench,
a training tray, or a tool board, and then checks whether a table is complete and correctly arranged.

1. Click **Setup Check** next to **Procedure**. This switches to semantic scanning and hides the procedure controls.
2. Enter the objects that belong in the setup under **Object descriptions** and press **Apply objects**.
3. Arrange the organized table, hold still, and press **Scan Objects**. Once the scan is accepted, type a
   **Setup name** and press **Capture Setup**. Each object's zone is saved, either to
   `backend/data/setups/<name>.json` or to Tiger Data when configured (below). Capturing an existing name updates
   that setup.
4. Later, choose the setup under **Saved setup**, hold the table still, and press **Check Setup**.

The result is deterministic, comparing labels and zones only:
- **Complete and correctly arranged**
- **Missing**: an expected object was not found
- **Unexpected**: a configured description that is not part of the setup was found on the table
- **Wrong zone**: the object was found, but in a different zone

A failed or ambiguous scan (worker error, duplicate or overlapping objects, or motion during the scan) produces no
verdict and clears the previous one. Moving the table after a check marks the result as out of date. No LLM or
Gemini call is involved. Setup Check never reads or writes the saved procedure, and procedure buttons and shortcuts
are disabled while it is active.

### Optional: saved setups in Tiger Data (Tiger Cloud / PostgreSQL)

By default, saved setups are local JSON files. To keep them in Tiger Cloud instead:

1. Copy the service connection string from Tiger Cloud's connection details into `.env` as `TIGER_DATABASE_URL`.
   It contains a password; `.env` is git-ignored, so never commit it.
2. Run the explicit migration and check. It creates the `teachback_setups` table if needed and verifies
   select/insert/upsert/read validation inside a transaction that is rolled back:

   ```bash
   npm run tiger:check
   ```

3. Restart `npm run dev`. The Setup panel shows **Storage: Tiger Data** only after the table loaded successfully.

TLS is always required (weaker `sslmode` values are upgraded to `require`, and the live connection is checked).
Connections have a 5 s connect timeout, and each transaction has a 5 s statement timeout. Setups load once at
startup into a validated in-memory cache; the database is contacted again only on **Capture Setup** and
**Retry connection**. If Tiger is configured but unreachable, the app still starts, Procedure mode works, and
the Setup panel shows **Tiger Data unavailable** with capture disabled. Nothing is silently written to local
files instead. Connection details never appear in the UI, `/api/health`, or logs. Only an error class such as
`OperationalError` is shown.

### Setup Check history and readiness (Tiger Data time-series)

With Tiger configured, every completed Setup Check is also appended as one immutable event to
`teachback_setup_checks`, a TigerData **hypertable** partitioned on `checked_at` (migration 002, applied by
`npm run tiger:check`). For the selected setup, the Setup panel shows:

- **History: Tiger Data** (or **History unavailable**) and whether *this* check was saved, is still saving,
  or was **not** saved (write failed, or the queue was full).
- **Readiness**: the percentage of checks that were complete, total / complete / needed-attention counts, the
  last check time, and hourly counts for the last 24 h, computed in the database with `time_bucket`.
- **Recent checks**: the five newest, with time and outcome.

The verdict never waits for history. Events go through one background writer with a bounded queue (32).
Overflow and failed writes are reported, never shown as saved. Each check has one `event_id`; retries are
idempotent (`ON CONFLICT DO NOTHING` on `(event_id, checked_at)`), and history is append-only. History reads
come from a cache refreshed at startup, after a successful write, on setup selection, and on **Refresh
history**. Without Tiger, history is shown as unavailable (local mode) and Setup Check itself works unchanged.

---

## Physical setup

- **Camera**: the laptop webcam (or a USB webcam), fixed, looking down at the table overhead or at an angle.
  Don't move it after you start teaching.
- **Objects**: up to four solid-colored objects, one each of **red, yellow, green, blue**. Blocks, cups, and caps
  all work, as long as each is one clear color.
- **Zones**: the screen shows three dashed rectangles, **Zone A / B / C**, side by side. Mark matching areas on the
  table with tape or paper so people can see them. The overlay tells you when an object's center is inside a zone.
- **Spacing**: keep objects in the same zone a little apart. Touching objects can look stacked from some angles.
- **Lighting**: even light, no strong colored reflections. If an object isn't detected, or is detected with a low
  percentage, use **Calibrate colors** (below).

Supported changes: move an object between zones, move it out of or into the zones, stack one object on another,
take an object off a stack, and move a stack (the top object is carried along).

## Running the demo

1. **Teach** (key `T`): arrange the starting layout and take your hands away. TeachBack captures it.
2. Do step 1, then hands off. Once the table has been still for about 0.7 s, the step appears in the timeline with
   before/after images. Repeat for four steps. Teaching finishes automatically after the fourth.
   - **Finish Teaching** (`F`) ends early with fewer steps. **Undo last step** (`U`) removes a mistaken step; put the
     objects back and show it again.
3. **Practice** (`P`): the next person restores the starting layout (the card lists anything out of place), then
   performs the steps.
   - Correct step: green card, "Step N done — now step N+1", spoken.
   - Mistake: red card with **Expected**, **Observed**, and a **Fix** instruction, spoken once. TeachBack does not
     advance. Undo the mistake (or move the object to the right spot) and carry on.
   - Hands moving or an object hidden is a *waiting* state, never an error.
4. **Restart Practice** (`P`) runs it again. **Reset** (`R`) clears everything so a new procedure can be taught.
   `M` mutes the voice.

The learned procedure is saved to `backend/data/` and survives a server restart.

### Calibrate colors

Click **Calibrate colors** in the header, then click each object in the camera view when its color chip is
highlighted (red → yellow → green → blue). Each click re-centers that color's HSV range on the pixels you clicked.
The calibration is saved to `backend/data/calibration.json`. **Reset colors** restores the defaults. The thresholds
themselves live in `backend/app/config.py`.

## Commands

| Command | What it does |
|---------|----------------|
| `npm install` | Install the root dev tools (`concurrently`). |
| `npm run setup` | Create `backend/.venv`, install Python requirements, install the frontend. |
| `npm run dev` | API on :8000 and the web app on :5173. |
| `npm run stop` | Free ports left busy by an earlier run. |
| `npm test` | pytest, then the frontend Vitest suite. |
| `npm run typecheck` | `tsc` for the frontend. |
| `npm run build` | Typecheck and production Vite build. |
| `npm run demo:check` | Three color-mode demos over WebSocket. Needs a running API. Default `ws://127.0.0.1:8000/ws`. Set `TEACHBACK_DEMO_URL` to aim it at another server. Use a temporary `TEACHBACK_DATA_DIR` if that server must not touch a saved procedure. |
| `npm run tiger:check` | Apply Tiger migrations and roll back a probe. Prints no connection string. |
| `npm run mat:check` | Offline mat landmark check. See above. |
| `npm run semantic:sizes` | LocateAnything size sweep at 448, 512, and 640. Needs WSL, CUDA, and the model. |

## Project layout

```
backend/app/
  config.py        thresholds, including semantic_max_dim validation
  vision.py        HSV segmentation → objects, zones, stacking; motion meter; color calibration
  mat.py           landmark geometry, tracking, canonical warp, 10% band mask
  detectors.py     color detector, semantic validation, latest-only scans
  locate_worker.py persistent WSL/CUDA worker, background preload, readiness probe
  stability.py     turns noisy frames into committed "stable" arrangements
  engine.py        TeachRecorder + PracticeEngine (deterministic pass/fail)
  describe.py      deterministic step / correction wording
  session.py       modes, mat routing, keyframes, persistence, Setup Check
  sources.py       camera ownership earned by valid frames
  setups.py        Setup Check types, local JSON store, deterministic checker
  history.py       SetupCheckEvent, readiness summary, bounded HistoryWriter
  library.py       SavedProcedure, local procedure library, deterministic default names
  tiger.py         Tiger setups + check-history + procedure repositories
backend/sql/       001_tiger_setups.sql, 002_tiger_setup_check_history.sql, 003_tiger_procedures.sql
backend/mat_check.py     npm run mat:check
backend/tiger_check.py   npm run tiger:check
backend/demo_check.py    three live color demos
frontend/src/      React UI (App, StatusCard, MatView, Timeline, ProcedureLibrary, SetupPanel, Overlay, Simulator)
benchmarks/locate_anything/size_sweep.py   npm run semantic:sizes
```

See [ARCHITECTURE.md](ARCHITECTURE.md), [DEMO_SCRIPT.md](DEMO_SCRIPT.md), and [PROGRESS.md](PROGRESS.md).

The local LocateAnything benchmark and constrained beta decision are documented in
[LOCATE_ANYTHING_EVAL.md](LOCATE_ANYTHING_EVAL.md). It has not replaced the color detector.

## Troubleshooting

- **"Camera unavailable"**: allow camera access in the browser's site settings, and close other apps using the
  webcam (Teams, Zoom). The page must be opened as `http://localhost:5173`, because browsers only allow cameras on
  localhost or HTTPS. Use **Simulator** to keep demoing.
- **A colored object isn't detected**: calibrate colors. Check the debug drawer for per-object confidence.
- **Semantic Objects says Loading model**: that is a model load, not a scan. Wait for **Model ready**. **Scan
  Objects** stays off until then. **Retry model** after **Model error**. Color mode still works.
- **A semantic scan is ambiguous**: use the four recommended objects, separate them, remove clutter and overlap,
  keep the landmarks visible, and scan again. LocateAnything does not expose confidence scores.
- **Status stuck on "Hands moving"**: something in view keeps changing, such as a person, a screen, or flicker. The
  debug drawer shows `motion %`. Raise `motion_threshold` in `config.py`, or aim the camera only at the table.
- **Steps merge together**: pause about a second with hands off between steps.
- **Port already in use**: `npm run stop`.

## Known limitations

- Color mode supports one object per color and four colors. Colors must stand out from the table and skin tones.
- Mat tracking needs all four stickers visible and a real camera. The simulator bypasses it. A lost, unsteady, or
  stale track produces no verdict.
- Semantic Objects is an opt-in, manual-scan beta: 2–6 uniquely described, separated objects; no stacking or
  automatic continuous tracking. The default input size is 448 because that size kept the four demo labels on the
  three evaluation photos. The broader 12-object benchmark reached 58.3%, so do not claim arbitrary-object
  reliability. Cold-start seconds from the size sweep are not a comparison of image sizes.
- **Retry model** is covered by automated worker tests. It has not been exercised by crashing a live GPU.
- The size-sweep boxes used to draw overlays are approximate. The 448 decision used label presence, not IoU.
  GPU memory in that report is device-wide `nvidia-smi`, not the worker process alone.
- Setup Check inherits the semantic beta's limits. A detector miss is reported as *Missing*: it can cause a false
  alarm, but never a false pass. Unexpected objects are only found among the configured object descriptions (at
  most 6 per scan, including the setup's own). Checks compare zones, not exact positions. While a semantic *procedure* is saved, its object descriptions stay locked (existing rule), so Setup
  Check scans with those descriptions until the procedure is reset.
- Tiger Data stores saved setups, check history, and named procedures (not keyframe images). Entries saved from
  another machine appear after a restart, **Retry connection**, or **Refresh**, not live. Moving from local JSON to
  Tiger does not copy existing local setups or procedures; save them again.
- Check history needs Tiger (it is off in local mode) and is append-only; there is no retention policy yet. The
  readiness view shows the selected setup only, with hourly buckets for the last 24 h. Events still queued when
  the server stops get up to 5 s to drain. A process crash loses queued (unsaved) events, and they are never
  shown as saved.
- Stacking is inferred from a single 2D view. Touching objects can look stacked. Objects that haven't moved since
  the last settled state are never newly counted as stacked, which removes most false positives.
- An object hidden inside an opaque container counts as occluded. Use open or marked container areas.
- Camera ownership is earned by a valid frame, not by opening a tab. Viewer tabs receive the owner's JPEG and the
  shared state.
- Automated tests cover the simulator, synthetic frames, mat geometry, and the semantic worker protocol. A full
  pass on a physical iPhone or OnePlus, under venue lighting, has not been recorded.
- Direct iPhone or Android browser capture needs a trusted HTTPS tunnel. The QR dialog says when the link is only
  insecure LAN HTTP.
