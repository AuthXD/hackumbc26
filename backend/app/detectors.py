"""Color detection and explicitly requested, separated-object semantic scans."""
from __future__ import annotations

from dataclasses import dataclass
import math
import tempfile
import threading
from pathlib import Path
from typing import Literal, Protocol

import cv2
import numpy as np

from .config import DATA_DIR, Settings, VisionConfig
from .locate_worker import LocateWorker
from .models import SceneObject, SceneState
from .vision import analyze_frame, assign_zones

DEFAULT_LABELS = ("blue water bottle", "brown wallet", "green smartwatch", "blue smartphone")


class AmbiguousScan(ValueError):
    pass


def parse_labels(text: str) -> tuple[str, ...]:
    labels = tuple(" ".join(part.split()) for part in text.split(","))
    if not 2 <= len(labels) <= 6 or any(not label or len(label) > 80 or any(c in label for c in "<>") for label in labels):
        raise ValueError("Enter 2–6 short, comma-separated object descriptions.")
    if len({label.casefold() for label in labels}) != len(labels):
        raise ValueError("Each object description must be unique.")
    return labels


def semantic_scene(detections: list[dict], labels: tuple[str, ...], cfg: VisionConfig, now: float,
                   allow_missing: bool = False) -> SceneState:
    """Validate raw boxes. `allow_missing` is only for Setup Check, where absence is the finding being reported;
    duplicates, unknown labels, invalid boxes and heavy overlap are still rejected as ambiguous."""
    labels = parse_labels(",".join(labels))
    vocabulary = {label.casefold(): label for label in labels}
    by_label = {}
    for detection in detections:
        key = " ".join(detection["label"].split()).casefold()
        if key not in vocabulary or key in by_label:
            raise AmbiguousScan("Duplicate or unexpected object. Use unique descriptions and separate the objects.")
        box = detection["bbox"]
        if len(box) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box):
            raise AmbiguousScan("Invalid object box. Rescan the table.")
        x1, y1, x2, y2 = box
        if not 0 <= x1 < x2 <= 1 or not 0 <= y1 < y2 <= 1:
            raise AmbiguousScan("Invalid object box. Rescan the table.")
        label = vocabulary[key]
        by_label[key] = SceneObject(kind="semantic", id=label, label=label, color=None, confidence=None,
                                   bbox=(x1, y1, x2 - x1, y2 - y1), center=((x1 + x2) / 2, (y1 + y2) / 2), zone=None)
    missing = [label for label in labels if label.casefold() not in by_label]
    if missing and not allow_missing:
        raise AmbiguousScan("Cannot see exactly one of: " + ", ".join(missing) + ". Keep every object fully visible.")
    objects = [by_label[label.casefold()] for label in labels if label.casefold() in by_label]
    for i, a in enumerate(objects):
        ax, ay, aw, ah = a.bbox
        for b in objects[i + 1:]:
            bx, by, bw, bh = b.bbox
            overlap = max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0, min(ay + ah, by + bh) - max(ay, by))
            if overlap / min(aw * ah, bw * bh) > .4:
                raise AmbiguousScan("Objects overlap heavily. Separate them before scanning; stacking is unsupported in beta.")
    assign_zones(objects, cfg)
    return SceneState(objects=objects, captured_at=now)


class Detector(Protocol):
    def detect(self, frame: np.ndarray, now: float, labels: tuple[str, ...] = (),
               allow_missing: bool = False) -> SceneState: ...
    def close(self) -> None: ...


class ColorDetector:
    def __init__(self, cfg: VisionConfig):
        self.cfg = cfg

    def detect(self, frame, now, labels=(), allow_missing=False):
        return analyze_frame(frame, self.cfg, now)

    def close(self):
        pass


class LocateAnythingDetector:
    def __init__(self, cfg: Settings, worker=None):
        self.cfg = cfg
        self.worker = worker or LocateWorker(cfg)

    def detect(self, frame, now, labels=(), allow_missing=False):
        labels = parse_labels(",".join(labels))
        ratio = min(1, 640 / max(frame.shape[:2]))
        small = cv2.resize(frame, (round(frame.shape[1] * ratio), round(frame.shape[0] * ratio)))
        directory = DATA_DIR / "semantic-scans"
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".png", dir=directory, delete=False) as f:
            path = Path(f.name)
        try:
            if not cv2.imwrite(str(path), small):
                raise OSError("Could not prepare semantic keyframe")
            return semantic_scene(self.worker.predict(path, labels), labels, self.cfg.vision, now, allow_missing)
        finally:
            path.unlink(missing_ok=True)

    def close(self):
        self.worker.close()


@dataclass
class ScanRequest:
    version: int
    frame: np.ndarray
    jpeg: bytes
    labels: tuple[str, ...]
    captured_at: float
    purpose: Literal["procedure", "setup_check"] = "procedure"


@dataclass
class ScanResult:
    request: ScanRequest
    scene: SceneState | None
    error: str = ""
    ambiguous: bool = False


class LatestScan:
    """One running inference plus one replaceable request. Never deliver superseded results."""
    def __init__(self, detector: Detector):
        self.detector = detector
        self.condition = threading.Condition()
        self.version = 0
        self.pending = None
        self.result = None
        self.thread = None
        self.closed = False
        self.busy = False

    def invalidate(self):
        with self.condition:
            self.version += 1
            self.pending = self.result = None

    def submit(self, frame, jpeg, labels, now, purpose="procedure"):
        with self.condition:
            if self.closed:
                raise RuntimeError("Scanner is closed")
            self.version += 1
            self.result = None
            self.pending = ScanRequest(self.version, frame.copy(), jpeg, labels, now, purpose)
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True, name="semantic-scanner")
                self.thread.start()
            self.condition.notify()

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                request, self.pending = self.pending, None
                self.busy = True
            try:
                scene = self.detector.detect(request.frame, request.captured_at, request.labels,
                                             allow_missing=request.purpose == "setup_check")
                result = ScanResult(request, scene)
            except Exception as exc:
                result = ScanResult(request, None, str(exc), isinstance(exc, AmbiguousScan))
            with self.condition:
                self.busy = False
                if not self.closed and request.version == self.version:
                    self.result = result
                self.condition.notify_all()

    def take_result(self):
        with self.condition:
            result, self.result = self.result, None
            return result

    def close(self):
        with self.condition:
            self.closed = True
            self.version += 1
            self.pending = self.result = None
            self.condition.notify()
        self.detector.close()
        if self.thread:
            self.thread.join(timeout=5)
