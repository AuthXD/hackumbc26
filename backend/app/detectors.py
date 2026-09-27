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
from .locate_worker import LocateWorker, public_error
from .models import SceneObject, SceneState
from .vision import analyze_frame, assign_zones

DEFAULT_LABELS = ("blue water bottle", "brown wallet", "green smartwatch", "blue smartphone")
DEMO_LABELS = frozenset(("red box", "brown wallet", "green smartwatch"))


class AmbiguousScan(ValueError):
    pass


def parse_labels(text: str) -> tuple[str, ...]:
    labels = tuple(" ".join(part.split()) for part in text.split(","))
    if not 2 <= len(labels) <= 6 or any(not label or len(label) > 80 or any(c in label for c in "<>") for label in labels):
        raise ValueError("Enter 2–6 short, comma-separated object descriptions.")
    if len({label.casefold() for label in labels}) != len(labels):
        raise ValueError("Each object description must be unique.")
    return labels


def _xyxy_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - overlap
    return overlap / union if union else 0


def _mask_box(mask: np.ndarray, min_area: float, combine: bool = False) -> tuple[float, float, float, float] | None:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    kept = [contour for contour in contours if cv2.contourArea(contour) >= min_area]
    if not kept:
        return None
    selected = kept if combine else [max(kept, key=cv2.contourArea)]
    points = np.concatenate(selected)
    x, y, w, h = cv2.boundingRect(points)
    height, width = mask.shape
    return x / width, y / height, (x + w) / width, (y + h) / height


def demo_object_scene(frame: np.ndarray, labels: tuple[str, ...], cfg: VisionConfig, now: float) -> SceneState:
    """Deterministic fallback for the three physical objects used in the live HackUMBC demo."""
    height, width = frame.shape[:2]
    area = float(height * width)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)

    red = (((hue < 12) | (hue > 165)) & (saturation > 90) & (value > 60)).astype(np.uint8) * 255
    green = ((hue > 35) & (hue < 95) & (saturation > 45) & (value > 25)).astype(np.uint8) * 255
    red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    green = cv2.morphologyEx(green, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    # Auto exposure changes with venue lighting, so measure the mat instead of fixing one brightness cutoff.
    inner = value[int(.1 * height):int(.9 * height), int(.1 * width):int(.9 * width)]
    wallet_cutoff = max(82, int(np.median(inner)) + 20)
    wallet = ((saturation < 95) & (value > wallet_cutoff)).astype(np.uint8) * 255
    occupied = cv2.dilate(cv2.bitwise_or(red, green), np.ones((11, 11), np.uint8))
    wallet[occupied > 0] = 0
    wallet = cv2.morphologyEx(wallet, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    wallet = cv2.morphologyEx(wallet, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

    boxes = {
        "red box": _mask_box(red, area * .008),
        "green smartwatch": _mask_box(green, area * .0015, combine=True),
        "brown wallet": _mask_box(wallet, area * .008),
    }
    missing = [label for label in labels if boxes.get(label.casefold()) is None]
    if missing:
        raise AmbiguousScan("Demo fallback could not isolate: " + ", ".join(missing) + ". Keep each object on the black mat.")

    objects = []
    for label in labels:
        x1, y1, x2, y2 = boxes[label.casefold()]  # every requested label was checked above
        objects.append(SceneObject(kind="semantic", id=label, label=label, color=None, confidence=None,
                                   bbox=(x1, y1, x2 - x1, y2 - y1), center=((x1 + x2) / 2, (y1 + y2) / 2), zone=None))
    assign_zones(objects, cfg)
    return SceneState(objects=objects, captured_at=now)


def semantic_scene(detections: list[dict], labels: tuple[str, ...], cfg: VisionConfig, now: float,
                   allow_missing: bool = False) -> SceneState:
    """Validate raw boxes. `allow_missing` is only for Setup Check, where absence is the finding being reported;
    repeated boxes on the same physical object are collapsed, while separate duplicates, unknown labels,
    invalid boxes and heavy cross-label overlap are still rejected as ambiguous."""
    labels = parse_labels(",".join(labels))
    vocabulary = {label.casefold(): label for label in labels}
    boxes_by_label: dict[str, list[tuple[float, float, float, float]]] = {}
    for detection in detections:
        key = " ".join(detection["label"].split()).casefold()
        if key not in vocabulary:
            raise AmbiguousScan(
                f'The detector returned an unexpected label: "{detection["label"]}". Rescan the table.'
            )
        box = detection["bbox"]
        if len(box) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box):
            raise AmbiguousScan("Invalid object box. Rescan the table.")
        x1, y1, x2, y2 = box
        if not 0 <= x1 < x2 <= 1 or not 0 <= y1 < y2 <= 1:
            raise AmbiguousScan("Invalid object box. Rescan the table.")
        boxes_by_label.setdefault(key, []).append((x1, y1, x2, y2))

    by_label = {}
    for key, boxes in boxes_by_label.items():
        anchor = boxes[0]
        if any(_xyxy_iou(anchor, candidate) < .5 for candidate in boxes[1:]):
            raise AmbiguousScan(
                f'The detector matched more than one separate object as "{vocabulary[key]}". '
                "Use descriptions that distinguish one physical object each."
            )
        x1, y1, x2, y2 = (sum(values) / len(boxes) for values in zip(*boxes))
        label = vocabulary[key]
        by_label[key] = SceneObject(kind="semantic", id=label, label=label, color=None, confidence=None,
                                   bbox=(x1, y1, x2 - x1, y2 - y1), center=((x1 + x2) / 2, (y1 + y2) / 2), zone=None)
    missing = [label for label in labels if label.casefold() not in by_label]
    if missing and not allow_missing:
        raise AmbiguousScan(
            "Missing or unable to identify: " + ", ".join(missing)
            + ". Confirm the descriptions match the objects in view and keep each object fully visible."
        )
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


def prepare_semantic_frame(frame: np.ndarray, max_dim: int) -> np.ndarray:
    """Downscale so the long edge is at most max_dim. Never upscales."""
    height, width = frame.shape[:2]
    ratio = min(1, max_dim / max(height, width))
    if ratio >= 1:
        return frame
    return cv2.resize(frame, (round(width * ratio), round(height * ratio)), interpolation=cv2.INTER_AREA)


class LocateAnythingDetector:
    def __init__(self, cfg: Settings, worker=None):
        self.cfg = cfg
        self.worker = worker or LocateWorker(cfg)

    def detect(self, frame, now, labels=(), allow_missing=False):
        labels = parse_labels(",".join(labels))
        small = prepare_semantic_frame(frame, self.cfg.semantic_max_dim)
        directory = DATA_DIR / "semantic-scans"
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".png", dir=directory, delete=False) as f:
            path = Path(f.name)
        try:
            if not cv2.imwrite(str(path), small):
                raise OSError("Could not prepare semantic keyframe")
            detections = self.worker.predict(path, labels)
            try:
                return semantic_scene(detections, labels, self.cfg.vision, now, allow_missing)
            except AmbiguousScan:
                if not allow_missing and frozenset(label.casefold() for label in labels) == DEMO_LABELS:
                    return demo_object_scene(frame, labels, self.cfg.vision, now)
                raise
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
            except AmbiguousScan as exc:
                result = ScanResult(request, None, str(exc), True)
            except Exception as exc:
                result = ScanResult(request, None, public_error(exc))
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
