"""Run saved keyframes through an isolated resident worker and write auditable measurements."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time

import cv2

from contract import adoption_gate, label_key, score, valid_box


def linux_path(path):
    path = Path(path).resolve()
    if os.name == "nt":
        return "/mnt/" + path.drive[0].lower() + path.as_posix()[2:]
    return str(path)


class Worker:
    def __init__(self, command, log, distro):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=log, text=True, encoding="utf-8", bufsize=1)
        self.messages = queue.Queue()
        self.pid = None
        self.distro = distro
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
        except Exception as exc:
            self.messages.put({"type": "error", "error": str(exc)})
        finally:
            self.messages.put({"type": "eof"})

    def receive(self, timeout):
        try:
            message = self.messages.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"Worker exceeded {timeout}s deadline") from None
        if message["type"] in ("error", "eof"):
            raise RuntimeError(f"Worker stopped: {message}")
        if "pid" in message:
            self.pid = message["pid"]
        return message

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def close(self):
        try:
            self.send({"type": "shutdown"})
            self.process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            if self.pid:
                command = ["kill", "-TERM", str(self.pid)]
                if os.name == "nt":
                    command = ["wsl", "-d", self.distro, "--", *command]
                subprocess.run(command, capture_output=True, timeout=10)
            self.process.kill()
            self.process.wait(timeout=5)
        finally:
            try:
                self.process.stdin.close()
            except OSError:
                pass  # flushing a pipe whose worker crashed can fail on Windows
            self.reader.join(timeout=2)
            self.process.stdout.close()


class GpuSampler:
    """Device-wide samples, explicitly not process-attributed under WDDM."""
    def __init__(self):
        self.samples = []
        self.error = None
        self.process = None
        self.reader = None

    def start(self):
        try:
            self.process = subprocess.Popen([
                "nvidia-smi", "--query-gpu=timestamp,index,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits", "-lms", "100"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.reader.start()
        except OSError as exc:
            self.error = str(exc)

    def _read(self):
        for line in self.process.stdout:
            try:
                stamp, index, used, total, utilization = [x.strip() for x in line.split(",")]
                self.samples.append({"timestamp": stamp, "gpu": int(index), "used_mib": float(used),
                                     "total_mib": float(total), "utilization": float(utilization)})
            except ValueError:
                self.error = line.strip()

    def stop(self):
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=5)
            self.reader.join(timeout=2)
            self.process.stdout.close()


def load_manifest(path):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    labels = [label_key(x) for x in manifest["labels"]]
    if not labels or len(set(labels)) != len(labels):
        raise ValueError("Labels must be non-empty and unique")
    ids = set()
    for scene in manifest["scenes"]:
        if scene["id"] in ids:
            raise ValueError("Scene IDs must be unique")
        ids.add(scene["id"])
        if not (path.parent / scene["image"]).is_file():
            raise ValueError(f"Missing image: {scene['image']}")
        for obj in scene["objects"]:
            valid_box(obj["bbox"])
            if label_key(obj["label"]) not in labels:
                raise ValueError("Annotation label absent from query labels")
    if not ids:
        raise ValueError("No scenes supplied")
    if not {label_key(x) for x in manifest.get("demo_critical_labels", [])} <= set(labels):
        raise ValueError("Demo-critical labels must be included in query labels")
    return manifest


def annotate(image, expected, predictions, output):
    image = image.copy()
    height, width = image.shape[:2]
    for kind, items, color in [("GT", expected, (0, 190, 0)), ("PRED", predictions, (220, 60, 220))]:
        for obj in items:
            x1, y1, x2, y2 = obj["bbox"]
            p, q = (round(x1 * width), round(y1 * height)), (round(x2 * width), round(y2 * height))
            cv2.rectangle(image, p, q, color, 2)
            cv2.putText(image, f"{kind}: {obj['label']}", (p[0], max(14, p[1] - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, .45, color, 1, cv2.LINE_AA)
    if not cv2.imwrite(str(output), image):
        raise OSError(f"Could not write {output}")


def report(result):
    gate = result["gate"]
    rows = ["# LocateAnything benchmark run", "", f"Recorded: {result['recorded_at']}", "",
            f"Adoption gate: **{'PASS' if gate['pass'] else 'NOT PASSED'}**", "",
            f"Purpose: {result['manifest']['purpose']}. Model: `{result['model']}`.",
            f"Mode: {result['mode']}; longest keyframe edge: {result['max_edge']} px.",
            f"Cold worker startup to ready: {result.get('cold_start_s')} seconds.",
            f"Median full keyframe round trip: {gate['median_keyframe_s']} seconds.",
            f"Demo-critical localization: {gate['correct']}/{gate['expected']} at label + IoU >= 0.5.",
            f"Ten consecutive requests without failure: {gate['ten_consecutive_ok']}.",
            f"Sampled device-wide peak GPU memory: {result['gpu_peak_mib']} MiB.",
            "GPU memory includes other apps; 100 ms sampling can miss brief peaks.",
            "Confidence is null: the installed C API exposes no confidence score.", ""]
    rows += [f"- {reason}" for reason in gate["reasons"]]
    if result.get("error"):
        rows += ["", f"Run error: {result['error']}"]
    rows += ["", "| Request | Scene | Status | Inference s | Round trip s | Correct/expected |",
             "|---|---|---|---|---|---|"]
    for row in result["requests"]:
        scores = row.get("score", {})
        rows.append(f"| {row['id']} | {row['scene_id']} | {row['status']} | {row.get('inference_s')} | "
                    f"{row.get('round_trip_s')} | {scores.get('correct', 0)}/{scores.get('expected', 0)} |")
    rows += ["", "Artifacts: `results.json`, `gpu.json`, `worker.stderr.log`, prepared keyframes and annotated PNGs.",
             "Accuracy uses only the first request per scene, preventing repeated frames from inflating coverage."]
    return "\n".join(rows) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="/home/authxd/models/locate-anything-q6_k.gguf")
    parser.add_argument("--library", type=Path, default=Path(__file__).parent / "build/liblocate_anything.so")
    parser.add_argument("--distro", default="Ubuntu")
    parser.add_argument("--mode", choices=["hybrid", "slow", "fast"], default="hybrid")
    parser.add_argument("--requests", type=int, default=10)
    parser.add_argument("--max-edge", type=int, default=640)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--startup-timeout", type=float, default=120)
    parser.add_argument("--provenance", type=Path, help="JSON with runtime revision and model SHA256")
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    if args.requests < len(manifest["scenes"]) or args.max_edge < 28 or args.timeout <= 0:
        parser.error("Requests must cover every scene; max-edge >= 28 and timeout > 0 required")
    args.output.mkdir(parents=True, exist_ok=False)
    prepared = []
    for index, scene in enumerate(manifest["scenes"]):
        source = args.manifest.parent / scene["image"]
        image = cv2.imread(str(source))
        if image is None:
            raise ValueError(f"Cannot decode {source}")
        ratio = min(1, args.max_edge / max(image.shape[:2]))
        image = cv2.resize(image, (round(image.shape[1] * ratio), round(image.shape[0] * ratio)))
        path = args.output / f"keyframe-{index:02}.png"
        if not cv2.imwrite(str(path), image):
            raise OSError(f"Cannot save {path}")
        prepared.append((scene, image, path))
    result = {"recorded_at": datetime.now(timezone.utc).isoformat(), "manifest": manifest,
              "model": args.model, "mode": args.mode, "max_edge": args.max_edge, "requests": [],
              "input_sha256": {s["image"]: hashlib.sha256((args.manifest.parent / s["image"]).read_bytes()).hexdigest()
                               for s in manifest["scenes"]}}
    command = ["python3", linux_path(Path(__file__).parent / "worker.py"), "--library", linux_path(args.library),
               "--model", args.model, "--mode", args.mode]
    if os.name == "nt":
        command = ["wsl", "-d", args.distro, "--", "env", "LA_DEVICE=CUDA0", *command]
    else:
        command = ["env", "LA_DEVICE=CUDA0", *command]
    result["worker_command"] = command
    if args.provenance:
        result["provenance"] = json.loads(args.provenance.read_text(encoding="utf-8"))
    gpu = GpuSampler()
    gpu.start()
    worker = None
    with (args.output / "worker.stderr.log").open("w", encoding="utf-8") as log:
        try:
            started = time.perf_counter()
            worker = Worker(command, log, args.distro)
            message = worker.receive(args.startup_timeout)
            if message["type"] == "starting":
                message = worker.receive(max(.01, args.startup_timeout - (time.perf_counter() - started)))
            if message["type"] != "ready":
                raise RuntimeError(f"Unexpected startup message: {message}")
            result["cold_start_s"] = time.perf_counter() - started
            result["ready"] = message
            print(f"Ready in {result['cold_start_s']:.3f}s", flush=True)
            for index in range(args.requests):
                scene, image, path = prepared[index % len(prepared)]
                row = {"id": index, "scene_id": scene["id"], "status": "error"}
                result["requests"].append(row)
                started = time.perf_counter()
                worker.send({"id": index, "image": linux_path(path), "labels": manifest["labels"]})
                reply = worker.receive(args.timeout)
                if reply.get("id") != index or reply["type"] != "result":
                    raise RuntimeError("Worker response does not match request")
                row.update(reply)
                row["round_trip_s"] = time.perf_counter() - started
                if row["status"] != "ok":
                    raise RuntimeError(row.get("error", "Detection failed"))
                row["score"] = score(scene["objects"], row["detections"])
                annotate(image, scene["objects"], row["detections"], args.output / f"annotated-{index:02}.png")
                print(f"Request {index}: {row['round_trip_s']:.3f}s, {len(row['detections'])} boxes", flush=True)
                (args.output / "partial.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        except Exception as exc:
            result["error"] = str(exc)
            print(f"Run failed: {exc}", flush=True)
        finally:
            if worker:
                worker.close()
            gpu.stop()
    result["gpu_peak_mib"] = max((s["used_mib"] for s in gpu.samples), default=None)
    result["gpu_baseline_mib"] = gpu.samples[0]["used_mib"] if gpu.samples else None
    result["gpu_sampling_error"] = gpu.error
    diagnostics = (args.output / "worker.stderr.log").read_text(encoding="utf-8", errors="replace")
    result["runtime_failure_lines"] = [line for line in diagnostics.splitlines()
                                       if re.search(r"out of memory|CUDA error|failed|GGML_ASSERT|aborted", line, re.I)]
    result["cuda_confirmed"] = "using device: CUDA0" in diagnostics
    result["gate"] = adoption_gate(manifest, result["requests"])
    if result["runtime_failure_lines"]:
        result["gate"]["pass"] = False
        result["gate"]["ten_consecutive_ok"] = False
        result["gate"]["reasons"].append("Native runtime reported a failure; inspect worker.stderr.log")
    if not result["cuda_confirmed"] or not gpu.samples:
        result["gate"]["pass"] = False
        result["gate"]["reasons"].append("CUDA execution or GPU-memory measurements not confirmed")
    if len(set(result["input_sha256"].values())) < 3:
        result["gate"]["pass"] = False
        result["gate"]["reasons"].append("Fewer than three distinct image contents")
    if result.get("error"):
        result["gate"]["pass"] = False
        result["gate"]["reasons"].append(result["error"])
    (args.output / "gpu.json").write_text(json.dumps(gpu.samples, indent=2), encoding="utf-8")
    (args.output / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (args.output / "REPORT.md").write_text(report(result), encoding="utf-8")
    print(f"Adoption gate: {result['gate']['pass']}; report: {args.output / 'REPORT.md'}", flush=True)
    return 0 if result["gate"]["pass"] else 2


if __name__ == "__main__":
    sys.exit(main())
