# LocateAnything evaluation, 2026-09-26

## Decision

**Not adopted. The required real tabletop accuracy benchmark is incomplete.**

The local model loads on the laptop GPU, returns plausible labeled boxes, and has
completed ten consecutive requests. These are runtime smoke results on existing
apple and laboratory images, not evidence for the user's demo objects. No production
detector interface, model scheduling, tracking or fallback integration has been enabled.
The original color detector and synthetic demonstration remain intact.

The missing input is at least three saved real tabletop photos covering ten object
categories, with independent ground-truth boxes. The user supplied nine intended
objects but no image folder yet. A pen is proposed as the tenth, not claimed as tested.

## Repository baseline

Started from clean `ca08776`, after directly reading PROGRESS, README, ARCHITECTURE,
DEMO_SCRIPT, package scripts, recent commits and the implementation. Baseline recorded
in commit `86c596c`:

- `npm test`: 68 passed, with a pytest-cache permission warning.
- `npm run typecheck`: passed.
- `npm run build`: passed.
- `npm run demo:check`: all three live runs passed, including recovery from skipped
  steps, wrong objects and wrong placements.

Used `$env:NPM_CONFIG_PREFIX='C:\Program Files\nodejs'` in this shell to avoid a
broken roaming npm launcher. Global settings were not changed. Because port 8000 was
occupied, a separate app ran on 8011 with a separate `TEACHBACK_DATA_DIR`; the demo
checker now accepts `TEACHBACK_DEMO_URL`, preserving the original default.

## Existing detector boundary

`frontend/src/useCamera.ts` acquires the webcam; `useFrameStream.ts` sends JPEGs at up
to 5 fps with one frame in flight. The simulator supplies canvas pixels to that same path.

`backend/app/main.py` receives frames and calls `Session.process_frame` in
`backend/app/session.py`. That method decodes/resizes the image, calls
`vision.analyze_frame`, then applies static-stack suppression, motion measurement and
`StabilityTracker`. `analyze_frame` wraps `detect_objects` into a `SceneState`.

The current detector does more than return boxes. It assigns color-based identity,
infers stacking and assigns zones. `SceneObject.bbox` is normalized **xywh**, while this
benchmark uses normalized **xyxy**. A future adapter must explicitly convert that format.
The new worker does not reuse `SceneObject.color` as an arbitrary object label.

Stable arrangements feed `TeachRecorder` and `PracticeEngine`. Persistence stores one
procedure, keyframes and calibration. Gemini only words learned transitions, with
before/after images and structured deltas; ElevenLabs voices engine events with browser
speech fallback. Neither service grades the procedure.

Object IDs must remain consistent across scenes before arbitrary objects can safely
drive the procedure engine. Identical colors, overlapping categories and multiple
instances are not solved by plugging a box list into the existing function.

## Runtime actually tested

- NVIDIA GeForce RTX 4060 Laptop GPU, 8188 MiB, driver 610.74, Ubuntu under WSL.
- User's installed [locate-anything.cpp](https://github.com/mudler/locate-anything.cpp),
  clean commit `77376ab332de918220f7a7e391542eefb5407c9f`, CUDA enabled.
- User's `/home/authxd/models/locate-anything-q6_k.gguf`, 5,512,244,960 bytes.
- SHA256 `818ba69f0dd886625b7d5889a25b3b1e9a004250251d02e89a038c44d2067c2b`.
- Hybrid decoding, greedy C++ implementation, batch one, eight CPU threads, maximum
  keyframe edge 640px. The existing C API uses a 256-token output budget.

This is a quantized port of [NVIDIA LocateAnything-3B](https://huggingface.co/nvidia/LocateAnything-3B),
not a fresh benchmark of the official BF16 Transformers runtime. Results apply only to
this measured configuration. The installed source/build was not modified; its compiled
archives were linked into a separate benchmark library.

`worker.py` owns the resident model in a separate process. JSONL requests contain saved
image paths and text labels. Native logs are separated from protocol output. Startup
and request deadlines are enforced by `evaluate.py`; failures terminate the run and
cannot silently satisfy the adoption gate.

The output includes normalized labels and xyxy boxes. **Confidence is null** with
`confidence_source=not_exposed_by_cpp_api`; the installed API has no per-box confidence.
Generating a fake constant or treating box geometry as probability would be misleading.

## Measurements obtained so far

| Run | Process-cold startup | Median prepared-keyframe round trip | Sampled GPU peak | Reliability |
|---|---:|---:|---:|---|
| Initial smoke, hybrid 640 | 15.287 s | 1.378 s | 4972 MiB | 10/10 completed |
| Verified runner, hybrid 640 | 11.049 s | 1.401 s | 4957 MiB | 10/10 completed |
| Intentional 50 ms deadline | 12.429 s | Not scored | 4957 MiB | Correctly rejected request 1 |
| Final runner, alongside app demo | 9.967 s | 1.336 s | 4957 MiB | 10/10 completed |

Cold startup includes process creation, WSL startup overhead and loading the model to
GPU. The OS disk cache was not flushed. Inference timing includes prepared PNG read,
native preprocessing/inference, JSON handling and IPC, but excludes offline image
resizing and overlay rendering. GPU readings are device-wide at 100ms intervals;
they include other programs and can miss brief peaks. After the timeout probe exited,
`nvidia-smi` showed 540 MiB used, confirming the worker released GPU allocation.

Smoke accuracy was 4/4 annotated instances over two unique images: three apples and
one foreground beaker. Ground truth was manually estimated before inference. Matching
requires the same normalized label and IoU >= 0.5, one prediction per target. Repeated
requests are excluded from unique-scene accuracy. This small result must not be
reported as demo accuracy or as testing ten object categories.

Saved overlays were inspected. The apple boxes align with the three apples. The beaker
box includes some of the projecting test tube but exceeds the defined IoU threshold.
The two smoke images are existing local test assets, not a representative tabletop corpus.

## Required adoption gate

| Requirement | Current evidence | Verdict |
|---|---|---|
| >=80% of demo-critical objects localized correctly | No saved photos of intended objects | Unmeasured |
| Median stable-keyframe inference <3 seconds | 1.336 s on final simple smoke run | Smoke only |
| Ten consecutive requests without crash/OOM | Three runs of ten completed | Smoke only |
| Ten common categories across several scenes | Two categories, two images | Incomplete |

The complete gate is a conjunction. Missing evidence fails closed. Accuracy means
recall over demo-critical ground-truth instances at exact normalized label + IoU >=0.5.
The benchmark also reports false positives and saves all predictions for inspection.
At least three distinct image contents and all intended critical labels are required.
Native failure diagnostics and missing CUDA/memory evidence prevent a passing verdict.

## Pending real-object test set

`benchmarks/locate_anything/demo-objects.json` records:

1. Blue wristband
2. Blue phone
3. Black case
4. Water bottle
5. AirPods in a black case
6. Brown wallet
7. Apple Watch with green bands
8. Gold coin
9. Black headphones
10. Pen, proposed to reach ten categories

Capture separated objects, then a rearranged scene with similar objects adjacent, then
a changed orientation/clutter scene. Annotate visible objects independently before
viewing model predictions. Include small-object and same-color confusions explicitly.
The `black case` description may need a physical distinction from the AirPods case;
measure the original descriptions first and record any prompt revision as a new run.

## Verification and artifacts

The separate benchmark has 13 tests covering coordinate validation, exact threshold
boundaries, one-to-one matching, duplicate detections, missing images, smoke-run
rejection, worker crashes and process timeouts. All pass with ResourceWarnings treated
as errors. Existing 68 app tests, typecheck and build also pass after the spike.

A live browser rehearsal on isolated ports 5174/8011 reached `Procedure complete` after
teaching red A-to-B, finishing early, restoring the layout and practicing the move.
The browser reported no console errors and approximately 5fps during the rehearsal.
After the spike, `npm run demo:check` again passed all three scenarios while the final
GPU benchmark ran concurrently. CUDA use was confirmed in native logs; no native
failure lines or GPU sampling errors were recorded in the final run.

Reproduction commands and dataset schema are in
[the benchmark README](benchmarks/locate_anything/README.md). Runtime fingerprints are
in [provenance.json](benchmarks/locate_anything/evidence/provenance.json).

Local annotated outputs:

- [Apple overlay](benchmarks/locate_anything/results/smoke-verified-640/annotated-00.png)
- [Beaker overlay](benchmarks/locate_anything/results/smoke-verified-640/annotated-01.png)
- [Raw verified results](benchmarks/locate_anything/results/smoke-verified-640/results.json)
- [Timeout evidence](benchmarks/locate_anything/results/timeout-probe/results.json)

Raw photos, generated overlays and build products remain local in ignored directories.
Compact result JSON and report snapshots are retained under `evidence/`, including
[final smoke results](benchmarks/locate_anything/evidence/smoke-results.json) and
[timeout results](benchmarks/locate_anything/evidence/timeout-results.json).

## Only after the gate passes

Introduce `Detector`, preserved `ColorDetector`, and `LocateAnythingDetector`, with
configuration, health/readiness, deadlines and graceful failure. Schedule localization
on settled scene changes, teach start, tracking-confidence loss, or explicit rescan.
Use lightweight tracking between these calls. Pixel motion must trigger rescans
independently of the existing color-based arrangement tracker, or unseen arbitrary
objects would never trigger the first localization.

Do not reuse cached boxes to declare a new stable step while a rescan is pending.
Do not switch semantic object identities to color IDs midway through an active
procedure; a fallback must pause that procedure or explicitly restart in color mode.
No Tiger Data, Gemini chat, organizations, locations or healthcare modes were added.
