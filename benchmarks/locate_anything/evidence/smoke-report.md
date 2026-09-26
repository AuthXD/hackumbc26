# LocateAnything benchmark run

Recorded: 2026-09-26T17:18:52.682252+00:00

Adoption gate: **NOT PASSED**

Purpose: runtime_smoke_only. Model: `/home/authxd/models/locate-anything-q6_k.gguf`.
Mode: hybrid; longest keyframe edge: 640 px.
Cold worker startup to ready: 9.966834299993934 seconds.
Median full keyframe round trip: 1.3357500999991316 seconds.
Demo-critical localization: 0/0 at label + IoU >= 0.5.
Ten consecutive requests without failure: True.
Sampled device-wide peak GPU memory: 4957.0 MiB.
GPU memory includes other apps; 100 ms sampling can miss brief peaks.
Confidence is null: the installed C API exposes no confidence score.

- Not a real-tabletop adoption dataset
- Fewer than ten annotated object categories
- Fewer than three distinct saved scenes
- Demo-critical objects lack annotations
- Demo-critical localization below 80% or unmeasured
- Fewer than three distinct image contents

| Request | Scene | Status | Inference s | Round trip s | Correct/expected |
|---|---|---|---|---|---|
| 0 | apples | ok | 1.5969115609999989 | 1.5980003000004217 | 3/3 |
| 1 | beaker | ok | 1.3344959860000074 | 1.3355503999919165 | 1/1 |
| 2 | apples | ok | 1.3698039940000086 | 1.3708701000141446 | 3/3 |
| 3 | beaker | ok | 1.3097129899999942 | 1.3110382000159007 | 1/1 |
| 4 | apples | ok | 1.3534928770000079 | 1.3545447000069544 | 3/3 |
| 5 | beaker | ok | 1.3044962360000056 | 1.3057923000014853 | 1/1 |
| 6 | apples | ok | 1.3346661150000045 | 1.3359498000063468 | 3/3 |
| 7 | beaker | ok | 1.2897163089999992 | 1.290604899986647 | 1/1 |
| 8 | apples | ok | 1.3627039189999977 | 1.363918700022623 | 3/3 |
| 9 | beaker | ok | 1.3107394239999905 | 1.3117247000045609 | 1/1 |

Artifacts: `results.json`, `gpu.json`, `worker.stderr.log`, prepared keyframes and annotated PNGs.
Accuracy uses only the first request per scene, preventing repeated frames from inflating coverage.
