# LocateAnything evaluation, 2026-09-26

## Decision

**The broad arbitrary-object gate did not pass. A constrained, opt-in manual-scan beta is adopted.**

Three OnePlus 12 tabletop photos on the intended black mat were independently annotated before inference. Across
separated, adjacent, and overlapping layouts, the model localized 21/36 instances at exact label + IoU >= 0.5
(58.3%). The separated layout scored 10/12, the adjacent layout 7/12, and the overlap/clutter layout 4/12. This is
below the 80% gate and does not support an “identify anything” claim.

The reliable subset—blue water bottle, brown wallet, green smartwatch, and blue smartphone—was integrated behind
`TEACHBACK_SEMANTIC_BETA=1`. It uses explicit user descriptions and manual scans only, rejects missing, duplicate,
unexpected, invalid, or heavily overlapping detections, and never fabricates confidence. Color mode remains the
default fallback. The beta preserves detector identity in saved procedures and forbids switching detectors during
an active procedure.

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
| OnePlus tabletop, 12 labels / 3 scenes | 11.015 s | 1.834 s | 4971 MiB | 10/10 completed |
| Live app, verified four-object scene | 21.5 s including cold load | 2.078 s warm rescan | ~5 GiB class | Passed |

Cold startup includes process creation, WSL startup overhead and loading the model to
GPU. The OS disk cache was not flushed. Inference timing includes prepared PNG read,
native preprocessing/inference, JSON handling and IPC, but excludes offline image
resizing and overlay rendering. GPU readings are device-wide at 100ms intervals;
they include other programs and can miss brief peaks. After the timeout probe exited,
`nvidia-smi` showed 540 MiB used, confirming the worker released GPU allocation.

The real OnePlus tabletop benchmark scored 21/36 (58.3%) over three layouts and 12 annotated descriptions. The
separated image scored 10/12; performance dropped as similar items became adjacent and objects overlapped. This
shows that the black mat, OnePlus camera, and moderate lighting differences are workable, while overlap and clutter
are the main failure mode. Raw photos, ground truth, and generated outputs remain local in ignored benchmark paths.

Earlier smoke accuracy was 4/4 annotated instances over two unique images: three apples and
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
| >=80% of demo-critical objects localized correctly | 21/36 = 58.3% across three real layouts | Failed |
| Median stable-keyframe inference <3 seconds | 1.834 s on the real tabletop run | Passed |
| Ten consecutive requests without crash/OOM | 10/10 on the real tabletop run | Passed |
| Ten common categories across several scenes | 12 descriptions across three distinct images | Passed |

The complete gate is a conjunction. Missing evidence fails closed. Accuracy means
recall over demo-critical ground-truth instances at exact normalized label + IoU >=0.5.
The benchmark also reports false positives and saves all predictions for inspection.
At least three distinct image contents and all intended critical labels are required.
Native failure diagnostics and missing CUDA/memory evidence prevent a passing verdict.

## Real-object test set

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

The three captured scenes cover separated objects, similar items adjacent, and overlap/clutter. Small-object and
same-color confusions were measured. The constrained beta therefore uses four large, visually distinct items and
requires separation rather than relaxing the failed gate.

## Verification and artifacts

The separate benchmark has 13 tests covering coordinate validation, exact threshold
boundaries, one-to-one matching, duplicate detections, missing images, smoke-run
rejection, worker crashes and process timeouts. All pass with ResourceWarnings treated
as errors. The integrated app has 91 passing tests, including semantic identity validation,
latest-request-wins scheduling, motion invalidation, ambiguous results, persistence, explicit fallback, worker
crashes, and timeouts. Typecheck and production build pass. A real WebSocket run found all four recommended objects
in `scene-1.jpg`, mapped their zones, preserved the semantic procedure across an explicit color fallback, and
completed a warm rescan in 2.078 seconds. The original three-run color demo still passes after integration.

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

## Constrained beta architecture

`ColorDetector` preserves the original path. `LocateAnythingDetector` talks to one lazy, resident WSL/CUDA worker
with startup/request deadlines and graceful termination. A `LatestScan` scheduler permits one active inference and
one replaceable pending request; superseded or motion-invalidated results cannot grade or teach. Semantic scans are
manual after the scene settles. A fallback must pause the active procedure before switching identity models.

No Tiger Data, Gemini chat, organizations, locations, or healthcare modes were added in this unit.
