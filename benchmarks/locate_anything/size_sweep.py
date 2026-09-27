"""Measure LocateAnything at 448, 512 and 640 px on the three demo tabletop photos."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time

import cv2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from evaluate import GpuSampler, Worker, annotate, linux_path  # noqa: E402

REPO = HERE.parents[1]
DEFAULT_IMAGES = [
    Path(os.environ.get("TEACHBACK_SEMANTIC_IMAGE_1") or r"C:\Users\Komal Tummala\Downloads\IMG20260926163419.jpg"),
    Path(os.environ.get("TEACHBACK_SEMANTIC_IMAGE_2") or r"C:\Users\Komal Tummala\Downloads\IMG20260926163438.jpg"),
    Path(os.environ.get("TEACHBACK_SEMANTIC_IMAGE_3") or r"C:\Users\Komal Tummala\Downloads\IMG20260926163455.jpg"),
]
LABELS = ["blue water bottle", "brown wallet", "green smartwatch", "blue smartphone"]
SIZES = (448, 512, 640)
SCENES = (
    {
        "id": "layout-a",
        "objects": [
            {"label": "blue water bottle", "bbox": [0.085, 0.16, 0.205, 0.72]},
            {"label": "blue smartphone", "bbox": [0.225, 0.38, 0.355, 0.51]},
            {"label": "green smartwatch", "bbox": [0.545, 0.09, 0.725, 0.22]},
            {"label": "brown wallet", "bbox": [0.695, 0.19, 0.825, 0.38]},
        ],
    },
    {
        "id": "layout-b",
        "objects": [
            {"label": "brown wallet", "bbox": [0.185, 0.28, 0.325, 0.48]},
            {"label": "green smartwatch", "bbox": [0.03, 0.68, 0.22, 0.82]},
            {"label": "blue water bottle", "bbox": [0.575, 0.18, 0.78, 0.78]},
            {"label": "blue smartphone", "bbox": [0.72, 0.22, 0.825, 0.42]},
        ],
    },
    {
        "id": "layout-c",
        "objects": [
            {"label": "brown wallet", "bbox": [0.125, 0.22, 0.26, 0.42]},
            {"label": "green smartwatch", "bbox": [0.20, 0.58, 0.42, 0.82]},
            {"label": "blue water bottle", "bbox": [0.58, 0.18, 0.76, 0.78]},
            {"label": "blue smartphone", "bbox": [0.72, 0.28, 0.825, 0.46]},
        ],
    },
)


def _norm(label: str) -> str:
    return " ".join(label.split()).casefold()


def label_sets(expected, detections):
    want = [_norm(o["label"]) for o in expected]
    got = [_norm(d["label"]) for d in detections]
    missed = [label for label in want if label not in got]
    extra = [label for label in got if label not in want]
    return want, got, missed, extra


def prepare(image: Path, max_edge: int, dest: Path) -> tuple[object, Path]:
    raw = cv2.imread(str(image))
    if raw is None:
        raise FileNotFoundError(f"Cannot decode {image}")
    ratio = min(1, max_edge / max(raw.shape[:2]))
    small = cv2.resize(raw, (round(raw.shape[1] * ratio), round(raw.shape[0] * ratio)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(dest), small):
        raise OSError(f"Cannot write {dest}")
    return small, dest


def compare_markdown(result: dict) -> str:
    rows = [
        "# LocateAnything input-size sweep",
        "",
        f"Recorded: {result.get('recorded_at')}",
        "",
        f"Labels: {', '.join(LABELS)}.",
        f"Images: {', '.join(s['id'] for s in SCENES)}.",
        f"Warm trials per image: {result.get('warm_trials')}.",
        f"Cold startup is a new worker load. Warm latency is the median of all post-ready inferences.",
        "Confidence is null: the C API does not expose it.",
        "",
        "| Size | Cold start s | Warm n | Warm median s | All 4 labels on every image | Missed | Extra | Timeouts |",
        "|---|---:|---:|---:|---|---|---|---:|",
    ]
    for size in result["sizes"]:
        rows.append(
            f"| {size['max_edge']} | {size.get('cold_start_s')} | {size.get('warm_n')} | "
            f"{size.get('warm_median_s')} | {size.get('preserved')} | "
            f"{', '.join(size.get('missed_any') or []) or '-'} | "
            f"{', '.join(size.get('extra_any') or []) or '-'} | {size.get('timeouts', 0)} |"
        )
    rows += ["", f"Chosen size: **{result.get('chosen_max_edge')}**.", result.get("chosen_reason", "")]
    if result.get("blocker"):
        rows += ["", f"Blocker: {result['blocker']}"]
    return "\n".join(rows) + "\n"


def choose(sizes: list[dict]) -> tuple[int | None, str]:
    ok = [s for s in sizes if s.get("preserved") and not s.get("error")]
    if not ok:
        return None, "No measured size kept every recommended demo object on every image."
    pick = min(ok, key=lambda s: s["max_edge"])
    return pick["max_edge"], (
        f"{pick['max_edge']} is the smallest size that detected every requested label on all "
        f"{len(SCENES)} images ({pick['warm_n']} warm trials, median {pick['warm_median_s']} s)."
    )


def run_size(worker_cmd, distro, max_edge, images, work, args, gpu):
    row = {"max_edge": max_edge, "requests": [], "timeouts": 0, "invalid": 0, "error": None}
    log = (work / f"worker-{max_edge}.log").open("w", encoding="utf-8")
    worker = None
    try:
        started = time.perf_counter()
        worker = Worker(worker_cmd, log, distro)
        message = worker.receive(args.startup_timeout)
        if message["type"] == "starting":
            message = worker.receive(max(0.01, args.startup_timeout - (time.perf_counter() - started)))
        if message["type"] != "ready":
            raise RuntimeError(f"Unexpected startup message: {message}")
        try:
            worker.send({"type": "probe"})
            probed = worker.receive(min(10.0, args.timeout))
            if probed.get("type") != "probed" or not probed.get("ok"):
                raise RuntimeError("Readiness probe failed")
        except Exception:
            pass  # older workers without probe still used the ready message
        row["cold_start_s"] = round(time.perf_counter() - started, 3)
        warm = []
        missed_any, extra_any = set(), set()
        preserved = True
        for trial in range(args.warm_trials):
            for scene, image in zip(SCENES, images):
                prepared, dest = prepare(image, max_edge, work / f"{max_edge}-{scene['id']}.png")
                item = {"trial": trial, "scene_id": scene["id"], "status": "error",
                        "requested": LABELS, "effective_size": list(prepared.shape[1::-1])}
                row["requests"].append(item)
                t0 = time.perf_counter()
                try:
                    worker.send({"id": f"{max_edge}-{trial}-{scene['id']}", "image": linux_path(dest), "labels": LABELS})
                    reply = worker.receive(args.timeout)
                except TimeoutError as exc:
                    item["error"] = str(exc)
                    row["timeouts"] += 1
                    preserved = False
                    continue
                except Exception as exc:
                    item["error"] = str(exc)
                    row["invalid"] += 1
                    preserved = False
                    continue
                item["round_trip_s"] = round(time.perf_counter() - t0, 3)
                item["inference_s"] = reply.get("inference_s")
                if reply.get("type") != "result" or reply.get("status") != "ok":
                    item["error"] = reply.get("error", "invalid response")
                    row["invalid"] += 1
                    preserved = False
                    continue
                item["status"] = "ok"
                detections = reply.get("detections") or []
                item["detected"] = [d.get("label") for d in detections]
                _want, _got, missed, extra = label_sets(scene["objects"], detections)
                item["missed"] = missed
                item["extra"] = extra
                item["confidence"] = None
                warm.append(item["round_trip_s"])
                missed_any.update(missed)
                extra_any.update(extra)
                if missed:
                    preserved = False
                if trial == 0:
                    annotate(prepared, scene["objects"], detections, work / f"annotated-{max_edge}-{scene['id']}.png")
        row["warm_latencies_s"] = warm
        row["warm_n"] = len(warm)
        row["warm_median_s"] = round(statistics.median(warm), 3) if warm else None
        row["preserved"] = preserved
        row["missed_any"] = sorted(missed_any)
        row["extra_any"] = sorted(extra_any)
    except Exception as exc:
        row["error"] = str(exc)
        row["preserved"] = False
    finally:
        if worker:
            worker.close()
        log.close()
    return row


def main(argv=None) -> int:
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "evidence")
    parser.add_argument("--work", type=Path, default=HERE / "results" / "size-sweep")
    parser.add_argument("--model", default="/home/authxd/models/locate-anything-q6_k.gguf")
    parser.add_argument("--library", type=Path, default=HERE / "build" / "liblocate_anything.so")
    parser.add_argument("--distro", default="Ubuntu")
    parser.add_argument("--warm-trials", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--startup-timeout", type=float, default=120)
    parser.add_argument("--images", type=Path, nargs="*", default=DEFAULT_IMAGES)
    args = parser.parse_args(argv)
    images = list(args.images)
    if len(images) != 3:
        print("Need exactly three evaluation images.", file=sys.stderr)
        return 2
    missing = [str(p) for p in images if not p.is_file()]
    result = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "labels": LABELS,
        "warm_trials": args.warm_trials,
        "images": [str(p) for p in images],
        "sizes": [],
        "blocker": None,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    if missing:
        result["blocker"] = "Missing evaluation images: " + "; ".join(missing)
        (args.output / "size-sweep.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (args.output / "size-sweep.md").write_text(compare_markdown(result), encoding="utf-8")
        print(result["blocker"], file=sys.stderr)
        return 2
    command = ["python3", linux_path(HERE / "worker.py"), "--library", linux_path(args.library),
               "--model", args.model, "--mode", "hybrid"]
    if os.name == "nt":
        command = ["wsl", "-d", args.distro, "--", "env", "LA_DEVICE=CUDA0", *command]
    else:
        command = ["env", "LA_DEVICE=CUDA0", *command]
    gpu = GpuSampler()
    gpu.start()
    try:
        for size in SIZES:
            print(f"Measuring {size} px…", flush=True)
            result["sizes"].append(run_size(command, args.distro, size, images, args.work, args, gpu))
    finally:
        gpu.stop()
    result["gpu_peak_mib"] = max((s["used_mib"] for s in gpu.samples), default=None)
    result["gpu_sampling_error"] = gpu.error
    chosen, reason = choose(result["sizes"])
    result["chosen_max_edge"] = chosen
    result["chosen_reason"] = reason
    if any(s.get("error") for s in result["sizes"]) and not chosen:
        result["blocker"] = next((s["error"] for s in result["sizes"] if s.get("error")), None)
    (args.output / "size-sweep.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (args.output / "size-sweep.md").write_text(compare_markdown(result), encoding="utf-8")
    print(compare_markdown(result), flush=True)
    return 0 if chosen else 2


if __name__ == "__main__":
    raise SystemExit(main())
