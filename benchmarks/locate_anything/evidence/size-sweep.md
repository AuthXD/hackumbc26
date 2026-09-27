# LocateAnything input-size sweep

Recorded: 2026-09-27T06:28:00.911333+00:00

Requested labels: blue water bottle, brown wallet, green smartwatch, blue smartphone.

Images (local Downloads copies, 4096 x 1864):

- layout-a: IMG20260926163419.jpg
- layout-b: IMG20260926163438.jpg
- layout-c: IMG20260926163455.jpg

Warm trials: 5 per image (15 inferences per size) after a new worker load. Cold startup is the time to `ready` plus the readiness probe. Warm latency is the median round-trip of those 15 inferences. Confidence is null; the C API does not expose it. GPU memory is device-wide nvidia-smi at 100 ms.

Effective prepared size at each maximum edge: 448 x 204, 512 x 233, 640 x 291.

| Size | Cold start s | Warm n | Warm median s | All 4 labels on every image | Missed | Extra | Timeouts | Invalid |
|---:|---:|---:|---:|---|---|---|---:|---:|
| 448 | 14.598 | 15 | 0.874 | yes | none | none | 0 | 0 |
| 512 | 12.034 | 15 | 1.127 | yes | none | none | 0 | 0 |
| 640 | 6.904 | 15 | 1.267 | yes | none | none | 0 | 0 |

Device-wide peak GPU memory during the sweep: 6220 MiB (includes other apps).

Chosen size: **448**.

448 is the smallest size that detected every requested label on all 3 images (15 warm trials, median 0.874 s). Detection preservation, not latency, decided the default.

Machine-readable results: `size-sweep.json`. Annotated first-trial overlays live under `benchmarks/locate_anything/results/size-sweep/` (gitignored).
