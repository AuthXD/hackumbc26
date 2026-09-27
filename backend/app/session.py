"""The single live demo session: mode switching, frame processing, keyframes, persistence."""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Literal

from .config import DATA_DIR, ColorRange, Settings, default_colors, settings
from .engine import Event, PracticeEngine, TeachRecorder, speak
from .detectors import ColorDetector, DEFAULT_LABELS, LatestScan, LocateAnythingDetector, parse_labels
from .models import Procedure, SceneState, StepText
from .stability import StabilityTracker, TrackerResult
from .vision import (
    MotionMeter,
    color_from_sample,
    decode_jpeg,
    downscale,
    sample_hsv,
    suppress_static_stacks,
)

Mode = Literal["idle", "teaching", "practicing"]

PROCEDURE_FILE = DATA_DIR / "procedure.json"
KEYFRAME_DIR = DATA_DIR / "keyframes"
CALIBRATION_FILE = DATA_DIR / "calibration.json"


class Session:
    def __init__(self, cfg: Settings = settings, persist: bool = True) -> None:
        self.cfg = cfg
        self.persist = persist
        self.lock = threading.RLock()
        self.mode: Mode = "idle"
        self.tracker = StabilityTracker(cfg.stability)
        self.meter = MotionMeter()
        self.recorder: TeachRecorder | None = None
        self.practice: PracticeEngine | None = None
        self.procedure: Procedure | None = None
        self.keyframes: dict[str, bytes] = {}
        self.last_scene: SceneState | None = None
        self.last_frame = None  # most recent downscaled BGR frame, for color calibration
        self.last_tracker: TrackerResult | None = None
        self.reference_scene: SceneState | None = None  # last committed stable state
        self.notice = ""  # one-line feedback for the last command
        self.pending_ai: list[int] = []  # learned step indexes awaiting an AI description
        self.detector_kind: Literal["color", "semantic"] = "color"
        self.color_detector = ColorDetector(cfg.vision)
        self.semantic_detector = LocateAnythingDetector(cfg)
        self.scanner = LatestScan(self.semantic_detector)
        self.semantic_labels = DEFAULT_LABELS
        self.scan_status = "idle"
        self.scan_message = "Hold the table still, then press Scan Objects."
        self.last_full_frame = None
        self.last_jpeg: bytes | None = None
        self.last_capture = 0.0
        self.still_since: float | None = None
        self.still_frames = 0
        if persist:
            self._load()
            self._load_calibration()
            if self.procedure and self.procedure.detector_kind == "semantic":
                self.semantic_labels = tuple(self.procedure.tracked_ids)
                if cfg.semantic_beta:
                    self.detector_kind = "semantic"

    # -- frames ---------------------------------------------------------------------------------

    def process_frame(self, jpeg: bytes, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        t0 = time.perf_counter()
        frame = decode_jpeg(jpeg)
        if frame is None:
            return {"type": "update", "error": "Could not decode frame"}
        small = downscale(frame, self.cfg.vision.process_width)
        with self.lock:
            self.last_full_frame, self.last_jpeg, self.last_capture = frame, jpeg, now
            self.last_frame = small
            if self.detector_kind == "semantic":
                events = self._semantic_frame(frame, small, now)
                snap = self.snapshot(events)
                snap["frameMs"] = round((time.perf_counter() - t0) * 1000, 1)
                return snap
            scene = self.color_detector.detect(small, now)
            scene = suppress_static_stacks(scene, self.reference_scene, self.cfg.vision)
            motion = self.meter.update(small)
            result = self.tracker.update(scene, motion, now)
            events: list[Event] = []
            if result.new_stable is not None:
                self.reference_scene = result.new_stable
                events = self._on_stable(result.new_stable, jpeg)
            self.last_scene, self.last_tracker, self.last_frame = scene, result, small
            snap = self.snapshot(events)
        snap["frameMs"] = round((time.perf_counter() - t0) * 1000, 1)
        return snap

    def _invalidate_scan(self, message="Scene changed. Hold still and press Scan Objects."):
        self.scanner.invalidate()
        self.last_scene = None
        self.scan_status, self.scan_message = "idle", message

    def _semantic_frame(self, frame, small, now):
        motion = self.meter.update(small)
        if motion > self.cfg.stability.motion_threshold:
            self.still_since, self.still_frames = now, 0
            self._invalidate_scan()
            self.last_tracker = TrackerResult("moving", motion)
            return []
        if self.still_since is None:
            self.still_since = now
        self.still_frames += 1
        stable_ms = (now - self.still_since) * 1000
        settled = self.still_frames >= self.cfg.stability.min_frames and stable_ms >= self.cfg.stability.stable_ms
        self.last_tracker = TrackerResult("stable" if settled else "settling", motion, stable_for_ms=stable_ms)
        if not settled:
            return []
        result = self.scanner.take_result()
        if result is None:
            return []
        drift = MotionMeter()
        drift.update(result.request.frame)
        if drift.update(frame) > self.cfg.stability.motion_threshold:
            self._invalidate_scan("Table changed during the scan. Hold still and scan again.")
            return []
        if result.scene is None:
            self.last_scene = None
            self.scan_status = "ambiguous" if result.ambiguous else "error"
            self.scan_message = result.error
            return []
        self.last_scene = self.reference_scene = result.scene
        self.scan_status = "valid"
        self.scan_message = "Scan accepted. After each move, hold still and press Scan Objects."
        return self._on_stable(result.scene, result.request.jpeg)

    def configure_detector(self, kind: str, labels: str = "") -> dict:
        with self.lock:
            try:
                if kind not in ("color", "semantic"):
                    raise ValueError("Unknown detector mode.")
                if self.mode != "idle":
                    raise ValueError("Pause the current procedure before switching detectors or object descriptions.")
                if kind == "semantic":
                    if not self.cfg.semantic_beta:
                        raise ValueError("Semantic Objects beta is not enabled in server configuration.")
                    requested = parse_labels(labels)
                    if self.procedure and self.procedure.detector_kind == "semantic" and set(requested) != set(self.procedure.tracked_ids):
                        raise ValueError("Reset the saved semantic procedure before changing its objects.")
                    self.semantic_labels = requested
                self.detector_kind = kind
                self._invalidate_scan()
                self.tracker.reset()
                self.meter.reset()
                self.still_since, self.still_frames = None, 0
                self.last_tracker = self.reference_scene = None
                self.notice = f"{'Semantic Objects beta' if kind == 'semantic' else 'Color mode'} selected. Saved procedure preserved."
            except ValueError as exc:
                self.notice = str(exc)
            return self.snapshot()

    def _cmd_scan(self) -> list[Event]:
        if self.detector_kind != "semantic":
            self.notice = "Select Semantic Objects beta before scanning."
        elif self.last_full_frame is None or not self._can_scan():
            self.notice = "Hold the table still before scanning."
        else:
            self.last_scene = None
            self.scanner.submit(self.last_full_frame, self.last_jpeg, self.semantic_labels, self.last_capture)
            self.scan_status, self.scan_message = "scanning", "Scanning objects. Keep the table still."
            self.notice = ""
        return []

    def _can_scan(self):
        return (self.last_full_frame is not None and self.still_since is not None
                and self.still_frames >= self.cfg.stability.min_frames
                and (self.last_capture - self.still_since) * 1000 >= self.cfg.stability.stable_ms)

    def _cmd_pause(self) -> list[Event]:
        proc = self.current_procedure()
        if proc is not None:
            self.procedure = proc
            self._save()
        self.mode = "idle"
        self.notice = "Procedure paused and preserved. You can now switch detector modes."
        return []

    def detector_status(self):
        return {"kind": self.detector_kind, "betaEnabled": self.cfg.semantic_beta,
                "labels": list(self.semantic_labels), "workerState": self.semantic_detector.worker.state,
                "scanState": self.scan_status, "message": self.scan_message,
                "canScan": self.detector_kind == "semantic" and self._can_scan(),
                "switchLocked": self.mode != "idle",
                "procedureKind": self.procedure.detector_kind if self.procedure else None}

    def close(self):
        self.scanner.close()

    def _on_stable(self, scene: SceneState, jpeg: bytes) -> list[Event]:
        if self.mode != "idle":
            self.notice = ""  # the status card takes over from the command's one-liner
        if self.mode == "teaching" and self.recorder:
            image = self._store_keyframe(jpeg)
            events = self.recorder.on_stable(scene, image)
            if self.recorder.steps and self.tracker.required is None:
                # From the first learned step on, a missing object means occlusion.
                self.tracker.required = self.recorder.tracked_set
            if self.detector_kind == "color":
                self.pending_ai += [e.step_index for e in events if e.kind == "step_learned"]
            if self.recorder.phase == "done":
                self._complete_teaching()
            return [e for e in events if e.kind == "speak"]
        if self.mode == "practicing" and self.practice:
            return self.practice.on_stable(scene)
        return []

    # -- commands -------------------------------------------------------------------------------

    def command(self, action: str) -> dict:
        with self.lock:
            handler = {
                "teach": self._cmd_teach,
                "finish": self._cmd_finish,
                "undo_step": self._cmd_undo_step,
                "practice": self._cmd_practice,
                "reset": self._cmd_reset,
                "scan": self._cmd_scan,
                "pause": self._cmd_pause,
            }.get(action)
            if self.detector_kind == "semantic" and action in ("teach", "practice", "undo_step", "reset", "pause"):
                self._invalidate_scan("Hold the table still, then press Scan Objects.")
            events = handler() if handler else []
            return self.snapshot(events)

    def _cmd_teach(self) -> list[Event]:
        self._clear_procedure()
        self.mode = "teaching"
        self.recorder = TeachRecorder(self.cfg.procedure.steps_per_procedure, self.cfg.procedure.min_objects)
        self.tracker.reset(required=None)
        self.notice = "Teaching: hold the starting layout still."
        return [speak("Teach mode. Show me the starting layout.")]

    def _cmd_finish(self) -> list[Event]:
        if self.mode != "teaching" or not self.recorder:
            return []
        if not self.recorder.steps:
            self.notice = "No steps learned yet — perform at least one step first."
            return [speak("I haven't learned any steps yet.")]
        self._complete_teaching()
        return [speak(f"Learned {len(self.procedure.steps)} steps. Ready for practice.", "success")]

    def _cmd_undo_step(self) -> list[Event]:
        if self.mode != "teaching" or not self.recorder:
            return []
        events = self.recorder.undo_last(self.last_scene)
        # Re-arm the tracker so the restored layout is seen as a fresh stable state.
        self.tracker.reset(required=self.recorder.tracked_set or None)
        return events

    def _cmd_practice(self) -> list[Event]:
        if not self.procedure or not self.procedure.steps:
            self.notice = "Teach a procedure first."
            return [speak("Teach me a procedure first.")]
        if self.procedure.detector_kind != self.detector_kind:
            self.notice = f"Switch to {self.procedure.detector_kind} mode to practice this saved procedure."
            return []
        self.mode = "practicing"
        self.recorder = None
        self.practice = PracticeEngine(self.procedure)
        self.tracker.reset(required=set(self.procedure.tracked_ids))
        self.notice = "Practice: set up the starting layout."
        return [speak("Practice mode. Set up the starting layout.")]

    def _cmd_reset(self) -> list[Event]:
        self._clear_procedure()
        self.mode = "idle"
        self.recorder = None
        self.tracker.reset()
        self.notice = "Reset. Ready to teach a new procedure."
        return []

    # -- color calibration ----------------------------------------------------------------------

    def calibrate(self, color: str, x: float, y: float) -> dict:
        """Re-center one color's HSV range on the pixel the user clicked."""
        with self.lock:
            if self.detector_kind != "color":
                self.notice = "Color calibration is only available in Color mode."
                return self.snapshot()
            idx = next((i for i, c in enumerate(self.cfg.vision.colors) if c.name == color), None)
            if idx is None or self.last_frame is None or not (0 <= x <= 1 and 0 <= y <= 1):
                self.notice = "Calibration needs a live frame and a known color."
                return self.snapshot()
            hsv = sample_hsv(self.last_frame, x, y)
            old = self.cfg.vision.colors[idx]
            self.cfg.vision.colors[idx] = color_from_sample(old.name, old.display, hsv)
            self.notice = f"Calibrated {color} (H{hsv[0]} S{hsv[1]} V{hsv[2]})."
            self._save_calibration()
            return self.snapshot()

    def reset_colors(self) -> dict:
        with self.lock:
            self.cfg.vision.colors = default_colors()
            if self.persist:
                CALIBRATION_FILE.unlink(missing_ok=True)
            self.notice = "Colors reset to defaults."
            return self.snapshot()

    def _save_calibration(self) -> None:
        if not self.persist:
            return
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        data = [{"name": c.name, "display": c.display, "bands": c.bands} for c in self.cfg.vision.colors]
        CALIBRATION_FILE.write_text(json.dumps(data, indent=2))

    def _load_calibration(self) -> None:
        if not CALIBRATION_FILE.exists():
            return
        try:
            data = json.loads(CALIBRATION_FILE.read_text())
            self.cfg.vision.colors = [
                ColorRange(d["name"], [(tuple(lo), tuple(hi)) for lo, hi in d["bands"]], d["display"]) for d in data
            ]
        except Exception as exc:  # a bad file must never stop the demo
            self.notice = f"Ignored unreadable color calibration: {exc}"

    # -- helpers --------------------------------------------------------------------------------

    def _complete_teaching(self) -> None:
        self.procedure = self.recorder.finish()
        self.mode = "idle"
        self.notice = f"Learned {len(self.procedure.steps)} steps. Press Practice."
        self._save()

    def _clear_procedure(self) -> None:
        self.procedure = None
        self.practice = None
        self.keyframes.clear()
        self.pending_ai.clear()
        if self.persist:
            PROCEDURE_FILE.unlink(missing_ok=True)
            for f in KEYFRAME_DIR.glob("*.jpg"):
                f.unlink(missing_ok=True)

    def _store_keyframe(self, jpeg: bytes) -> str:
        key = uuid.uuid4().hex[:12]
        self.keyframes[key] = jpeg
        return f"/api/keyframes/{key}.jpg"

    def keyframe(self, key: str) -> bytes | None:
        return self.keyframes.get(key)

    def current_procedure(self) -> Procedure | None:
        if self.mode == "teaching" and self.recorder:
            return self.recorder.procedure()
        return self.procedure

    def take_pending_ai(self) -> list[tuple[int, str | None]]:
        """Learned steps that still need AI wording, as (index, after_image) identity pairs."""
        with self.lock:
            proc = self.current_procedure()
            out = [
                (i, proc.steps[i].after_image)
                for i in self.pending_ai
                if proc and i < len(proc.steps) and proc.steps[i].ai_description is None
            ]
            self.pending_ai.clear()
            return out

    def step_for_ai(self, index: int, after_image: str | None):
        """The step to describe, or None if it was undone/replaced meanwhile."""
        with self.lock:
            proc = self.current_procedure()
            if not proc or index >= len(proc.steps) or proc.steps[index].after_image != after_image:
                return None
            step = proc.steps[index]
            before, after = self.step_images(index)
            return step, before, after

    def set_ai_description(self, index: int, after_image: str | None, text: StepText) -> bool:
        with self.lock:
            proc = self.current_procedure()
            if not proc or index >= len(proc.steps) or proc.steps[index].after_image != after_image:
                return False  # the step changed while Gemini was thinking
            proc.steps[index].ai_description = text
            if self.procedure is not None:
                self._save()
            return True

    def step_images(self, index: int) -> tuple[bytes | None, bytes | None]:
        proc = self.current_procedure()
        if not proc or index >= len(proc.steps):
            return None, None
        step = proc.steps[index]
        key = lambda url: url.rsplit("/", 1)[-1].removesuffix(".jpg") if url else ""  # noqa: E731
        return self.keyframes.get(key(step.before_image)), self.keyframes.get(key(step.after_image))

    # -- persistence ----------------------------------------------------------------------------

    def _save(self) -> None:
        if not self.persist or not self.procedure:
            return
        KEYFRAME_DIR.mkdir(parents=True, exist_ok=True)
        PROCEDURE_FILE.write_text(json.dumps(self.procedure.to_json(), indent=2))
        referenced: set[str] = set()
        for s in self.procedure.steps:
            for url in (s.before_image, s.after_image):
                if url:
                    referenced.add(url.rsplit("/", 1)[-1].removesuffix(".jpg"))
        for key in referenced:
            if key in self.keyframes:
                (KEYFRAME_DIR / f"{key}.jpg").write_bytes(self.keyframes[key])

    def _load(self) -> None:
        if not PROCEDURE_FILE.exists():
            return
        try:
            self.procedure = Procedure.model_validate(json.loads(PROCEDURE_FILE.read_text()))
            for f in KEYFRAME_DIR.glob("*.jpg"):
                self.keyframes[f.stem] = f.read_bytes()
            self.notice = f"Loaded saved procedure ({len(self.procedure.steps)} steps)."
        except Exception as exc:  # a corrupt file must never stop the demo
            self.procedure = None
            self.notice = f"Ignored unreadable saved procedure: {exc}"

    # -- output ---------------------------------------------------------------------------------

    def snapshot(self, events: list[Event] | None = None) -> dict:
        proc = self.current_procedure()
        return {
            "type": "update",
            "mode": self.mode,
            "detector": self.detector_status(),
            "notice": self.notice,
            "scene": self.last_scene.to_json() if self.last_scene else None,
            "zones": [
                {"id": z.id, "label": z.label, "x": z.x, "y": z.y, "w": z.w, "h": z.h}
                for z in self.cfg.vision.zones
            ],
            "colors": {c.name: c.display for c in self.cfg.vision.colors},
            "tracker": self.last_tracker.to_json() if self.last_tracker else None,
            "teach": self.recorder.to_json() if self.recorder and self.mode == "teaching" else None,
            "procedure": _procedure_json(proc),
            "practice": self.practice.state.model_dump(by_alias=True, mode="json", exclude={"observed_state"})
            if self.practice and self.mode == "practicing"
            else None,
            "events": [e.to_json() for e in events or [] if e.kind == "speak"],
            "integrations": {
                "gemini": bool(self.cfg.gemini_api_key),
                "elevenlabs": bool(self.cfg.elevenlabs_api_key),
            },
        }


def _procedure_json(proc: Procedure | None) -> dict | None:
    if proc is None:
        return None
    return {
        "detectorKind": proc.detector_kind,
        "trackedIds": proc.tracked_ids,
        "steps": [
            s.model_dump(by_alias=True, mode="json", exclude={"before_state", "after_state"}) for s in proc.steps
        ],
    }
