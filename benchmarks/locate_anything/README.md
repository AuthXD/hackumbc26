# LocateAnything local benchmark

This spike is separate from `backend/app`. It reuses the installed Ubuntu CUDA
`locate-anything.cpp` build and Q6_K model. The color detector and simulator are unchanged.
No model calls are attached to the video stream.

## Reproduce on this laptop

From the repository root in PowerShell:

```powershell
wsl -d Ubuntu -- python3 /mnt/c/AuthXD/hackumbc26/benchmarks/locate_anything/build_shared.py --source /home/authxd/locate-anything.cpp --output /mnt/c/AuthXD/hackumbc26/benchmarks/locate_anything/build/liblocate_anything.so
```

The linker reuses the existing position-independent static archives and CUDA link
arguments. It creates a separate library; it does not edit the installed source/build.
On another machine, build the port with `LA_SHARED=ON` and `LA_GGML_CUDA=ON`, and pass
the resulting library to `--library`. This script is specifically for the existing
CMake Unix Makefiles layout, not a general installer.

```powershell
wsl -d Ubuntu -- python3 /mnt/c/AuthXD/hackumbc26/benchmarks/locate_anything/provenance.py --source /home/authxd/locate-anything.cpp --model /home/authxd/models/locate-anything-q6_k.gguf --library /mnt/c/AuthXD/hackumbc26/benchmarks/locate_anything/build/liblocate_anything.so --output /mnt/c/AuthXD/hackumbc26/benchmarks/locate_anything/evidence/provenance.json
& backend/.venv/Scripts/python.exe -m unittest discover -s benchmarks/locate_anything -p 'test_*.py' -v
& backend/.venv/Scripts/python.exe benchmarks/locate_anything/evaluate.py --manifest benchmarks/locate_anything/smoke.json --output benchmarks/locate_anything/results/my-smoke --requests 10 --provenance benchmarks/locate_anything/evidence/provenance.json
```

Use a new output directory on each run. Exit 0 means the full adoption gate passed;
exit 2 means it did not, including a successful smoke run lacking adoption data.
PowerShell command wrappers may surface any nonzero exit as 1; check the report.
Worker stderr, raw outputs, normalized boxes, timings, GPU samples, input hashes,
prepared keyframes and annotated PNGs are saved. No API keys or network are needed.
The worker loads once and handles requests sequentially. Startup defaults to a 120s
deadline and each inference to 60s; timeout/crash stops the run and terminates the worker.

## Real tabletop dataset

The requested object list is in `demo-objects.json`, with pen proposed as the tenth
category. The nine user-specified categories are demo-critical. Confirm the tenth
object and adjust the label if needed. Ground truth must be drawn before looking at
predictions. Do not use the model's own boxes as ground truth.

Save at least three genuine photos, ideally 1280px or larger, under `data/tabletop/`:

1. All ten objects separated, hands out of frame.
2. Rearrange them; place the blue wristband beside the blue phone and the two cases nearby.
3. Change orientation and spacing, add realistic clutter, keep each evaluated object identifiable.

Copy `demo-objects.json` to `data/tabletop/manifest.json`. Fill `scenes` with objects
actually visible in each photo. Coordinates are `[left, top, right, bottom]` divided
by the original image width/height, not pixel xywh. Example of one scene entry:

```json
{"id":"separated","image":"scene-1.jpg","objects":[
  {"label":"blue phone","bbox":[0.10,0.20,0.30,0.55]}
]}
```

Those numbers are illustrative only. Use measured boxes from the actual image.
Images resolve relative to the manifest, so it can also live in a user-supplied folder.
All annotated labels must be present in `labels`. Lowercase and whitespace are normalized;
synonyms are not silently accepted. Every labeled instance can match only one prediction.
Repeated requests do not inflate the unique-scene accuracy calculation.

```powershell
& backend/.venv/Scripts/python.exe benchmarks/locate_anything/evaluate.py --manifest benchmarks/locate_anything/data/tabletop/manifest.json --output benchmarks/locate_anything/results/tabletop-01 --requests 10 --provenance benchmarks/locate_anything/evidence/provenance.json
```

The fixed initial setting is hybrid decoding, greedy port behavior, eight CPU threads,
640px maximum image edge, batch size one. Any change to resolution, mode, quantization,
prompts or runtime needs a separately identified run. The C API uses a 256-token output
budget; this is a limit for dense scenes, not a reason to omit missed objects.

## Contract and gate

Each detection contains `label`, normalized xyxy `bbox`, `confidence: null`, and
`confidence_source: "not_exposed_by_cpp_api"`. The installed API returns no probability.
The color detector's solidity score is not substituted for model confidence.
Instance identity, tracking, zone assignment and stacking are deliberately outside this spike.

Correct localization means exact normalized label plus IoU >= 0.5 against independent
ground truth. Adoption requires >=80% recall of demo-critical annotated instances,
median prepared-keyframe round trip <3 seconds, and every request in a run of at least
ten succeeding without restart or native runtime failure. Also require at least ten
annotated categories, three distinct real tabletop images, measured GPU memory, and CUDA
confirmation. Predictions duplicated for the same target are recorded as false positives.

Round trip includes worker IPC, PNG read/preprocessing, model inference and normalization.
Resizing/encoding saved test inputs and drawing overlays happen outside the timer.
Startup is a new process/model load; OS disk-cache state is uncontrolled. GPU memory is
device-wide, sampled every 100ms, and includes other apps. It is not exact process peak VRAM.

`smoke.json` uses existing local apple and lab images to check runtime behavior only.
It can never satisfy the adoption gate. The images are intentionally untracked; on this
laptop their sources are `/home/authxd/apples.jpg` and `/home/authxd/test.png`.

## Sources

- [NVIDIA model and inference contract](https://huggingface.co/nvidia/LocateAnything-3B)
- [Installed C++ port](https://github.com/mudler/locate-anything.cpp)
- Runtime commit and model/library checksums: `evidence/provenance.json`.
