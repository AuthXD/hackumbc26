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

No camera handy? Click **Simulator** in the header, or open http://localhost:5173/?sim. You get a synthetic
tabletop whose pixels go through exactly the same vision pipeline.

### Optional API keys

Copy `.env.example` to `.env` in the repo root and fill in any keys you have, then restart `npm run dev`.

| Key | What it adds | Without it |
|-----|--------------|-----------|
| `GEMINI_API_KEY` | Gemini words each learned step from the before/after keyframes plus the structured change | Deterministic text, e.g. "Move the red object from Zone A to Zone B." |
| `ELEVENLABS_API_KEY` | Natural voice for corrections and step completions | Browser `speechSynthesis` |

Neither service ever decides whether a step was correct. That decision is always deterministic.

### Optional Semantic Objects beta

The original four-color path remains the default and the safest judging demo. To enable manual recognition of
ordinary objects with the local LocateAnything model, set `TEACHBACK_SEMANTIC_BETA=1` in `.env` and restart the
server. The UI then offers **Semantic Objects · Beta**.

Use 2–6 unique descriptions such as `blue water bottle, brown wallet, green smartwatch, blue smartphone`. Keep
every object separated and fully visible on the black mat. In Teach or Practice, wait for the table to settle and
press **Scan Objects** after the starting layout and after every move. The first scan loads the GPU model and can
take roughly 20 seconds; warm scans take about 2 seconds on the tested RTX 4060 laptop. Stacking is not supported
in this mode. If a scan fails, pause the procedure before explicitly switching back to Color mode; the learned
semantic procedure is preserved.

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

## Verifying

```bash
npm test
```

This runs 91 backend tests: the sequence engine, vision on synthetic JPEG frames, the stability filter, end-to-end
sessions with a simulated hand, semantic scan scheduling and worker failures, integration fallbacks, and an app
smoke test.

```bash
npm run demo:check
```

With `npm run dev` running, this plays the full judging demo three times against the live server over the real
WebSocket. Each run uses a different procedure and a different mistake (skipped step, wrong object, wrong zone).

```bash
npm run typecheck
```

## Project layout

```
backend/app/
  config.py        every threshold: HSV colors, zones, stability timing, motion gate
  vision.py        HSV segmentation → objects, zones, stacking; motion meter; color calibration
  detectors.py     color detector adapter + conservative semantic scan validation/scheduling
  locate_worker.py lazy persistent WSL/CUDA LocateAnything worker with deadlines
  stability.py     turns noisy frames into committed "stable" arrangements
  engine.py        TeachRecorder + PracticeEngine (deterministic pass/fail)
  describe.py      deterministic step / correction wording
  session.py       modes, keyframes, persistence
  integrations.py  Gemini step wording, ElevenLabs voice (both optional)
  main.py          FastAPI: /ws, /api/speak, /api/keyframes
backend/tests/     pytest suite
backend/demo_check.py  live 3-run demo check
frontend/src/      React UI (App, StatusCard, Timeline, Overlay, Simulator, speech)
```

See [ARCHITECTURE.md](ARCHITECTURE.md), [DEMO_SCRIPT.md](DEMO_SCRIPT.md), and [PROGRESS.md](PROGRESS.md).

The local LocateAnything benchmark and constrained beta decision are documented in
[LOCATE_ANYTHING_EVAL.md](LOCATE_ANYTHING_EVAL.md). It has not replaced the color detector.

## Troubleshooting

- **"Camera unavailable"**: allow camera access in the browser's site settings, and close other apps using the
  webcam (Teams, Zoom). The page must be opened as `http://localhost:5173`, because browsers only allow cameras on
  localhost or HTTPS. Use **Simulator** to keep demoing.
- **A colored object isn't detected**: calibrate colors. Check the debug drawer for per-object confidence.
- **A semantic scan is ambiguous**: use the four recommended objects, separate them, remove clutter/overlap, keep
  the camera fixed, and scan again. LocateAnything does not expose confidence scores in this build.
- **Status stuck on "Hands moving"**: something in view keeps changing, such as a person, a screen, or flicker. The
  debug drawer shows `motion %`. Raise `motion_threshold` in `config.py`, or aim the camera only at the table.
- **Steps merge together**: pause about a second with hands off between steps.
- **Port already in use**: `npm run stop`.

## Known limitations

- Color mode supports one object per color and four colors. Colors must stand out from the table and skin tones.
- Semantic Objects is an opt-in, manual-scan beta: 2–6 uniquely described, separated objects; no stacking or
  automatic continuous tracking. The broader 12-object benchmark reached 58.3%, so use the verified four-object
  set rather than claiming arbitrary-object reliability.
- Stacking is inferred from a single 2D view. Touching objects can look stacked. Objects that haven't moved since
  the last settled state are never newly counted as stacked, which removes most false positives.
- An object hidden inside an opaque container counts as occluded. Use open or marked container areas.
- The newest browser tab owns the camera. Other tabs just watch.
- Tested with the simulator and synthetic frames. Tune real webcam lighting with the calibration step before judging.
- iPhone Safari needs HTTPS for camera access and isn't set up. The laptop webcam is the supported path.
