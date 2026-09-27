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
- **SetupRepository** protocol (`list` / `get` / `save` / `errors`). `JsonSetupRepository` stores one file per
  setup in `DATA_DIR/setups/`, writes atomically, and skips and reports malformed files. It is the only
  implementation for now; a database-backed repository can replace it without touching the session.
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
- `demo_check.py`: three full demos against the live server.
