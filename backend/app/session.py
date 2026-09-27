"""The single live demo session: mode switching, frame processing, keyframes, persistence."""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Literal

import cv2
import numpy as np

from .config import DATA_DIR, ColorRange, Settings, default_colors, settings
from .engine import Event, PracticeEngine, TeachRecorder, speak
from .detectors import ColorDetector, DEFAULT_LABELS, LatestScan, LocateAnythingDetector, parse_labels
from .history import CheckHistoryRepository, HistoryWriter, SetupCheckEvent, create_check_history_repository
from .mat import MatFrame, MatService, canonical_vision, in_workspace
from .models import Procedure, SceneState, StepText
from .setups import (
    SavedSetup,
    SetupCheckResult,
    SetupRepository,
    SetupStorageError,
    check_setup,
    create_setup_repository,
)
from .stability import StabilityTracker, TrackerResult
from .vision import (
    MotionMeter,
    analyze_frame,
    assign_zones,
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
MAT_DIR = DATA_DIR / "mat"  # per-camera mat calibrations; never touches procedures or setups
SETUP_DIR = DATA_DIR / "setups"  # kept apart from procedure.json so Setup Check can never touch it

PROCEDURE_ACTIONS = ("teach", "finish", "undo_step", "practice", "reset", "pause")


class Session:
    def __init__(self, cfg: Settings = settings, persist: bool = True,
                 setup_repository: SetupRepository | None = None,
                 history_repository: CheckHistoryRepository | None = None) -> None:
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
        self.on_detector_change = None  # main.py broadcasts when the worker leaves loading
        self.last_full_frame = None
        self.last_jpeg: bytes | None = None
        self.last_capture = 0.0
        self.still_since: float | None = None
        self.still_frames = 0
        self.workspace: Literal["procedure", "setup"] = "procedure"
        # Local JSON, or Tiger Cloud when TIGER_DATABASE_URL is set. The choice lives in setups.py; no SQL here.
        self.setups: SetupRepository = setup_repository or create_setup_repository(cfg, SETUP_DIR)
        # Check history: Tiger hypertable when configured, otherwise explicitly disabled. All history I/O
        # happens on the writer's single background thread, never under Session.lock.
        self.history: CheckHistoryRepository = history_repository or create_check_history_repository(cfg)
        self.history_writer = HistoryWriter(self.history, maxsize=cfg.history_queue_size)
        self.setup_event: SetupCheckEvent | None = None  # the one event for the current verdict
        self.selected_setup_id: str | None = None
        self.setup_result: SetupCheckResult | None = None
        self.setup_result_stale = False  # the table moved after the last check
        self.pending_check: str | None = None  # setup id a submitted check scan belongs to
        self.last_scan_purpose: str | None = None
        # Mat stabilization: which camera is supplying frames and what the mat tracker made of the last one.
        self.mat = MatService(cfg.mat, MAT_DIR if persist else None)
        self.frame_source = "webcam"
        self.last_raw_frame = None  # the owner's untouched frame (calibration clicks refer to it)
        self.mat_frame: MatFrame | None = None
        self.mat_mode: tuple[str, bool] | None = None  # (source, bypass) of the previous frame
        self.mat_view_jpeg: bytes | None = None  # latest stabilized top-down mat image
        self.mat_view_seq = 0
        self.vision_active = cfg.vision  # zones of the picture currently evaluated
        self.scan_pose: tuple[str, int, object] | None = None  # (source, epoch, corners) at scan submit
        if persist:
            self._load()
            self._load_calibration()
            if self.procedure and self.procedure.detector_kind == "semantic":
                self.semantic_labels = tuple(self.procedure.tracked_ids)
                if cfg.semantic_beta:
                    self.detector_kind = "semantic"

    # -- frames ---------------------------------------------------------------------------------

    def process_frame(self, jpeg: bytes, now: float | None = None, source: str = "webcam") -> dict:
        now = time.time() if now is None else now
        t0 = time.perf_counter()
        frame = decode_jpeg(jpeg)
        if frame is None:
            return {"type": "update", "error": "Could not decode frame"}
        with self.lock:
            if self.detector_kind == "semantic":
                self._kick_semantic_preload()
            image, view_jpeg, untrusted = self._stabilize(frame, jpeg, source, now)
            if image is None:  # mat tracking lost: no scene, no verdicts, no commits
                snap = self.snapshot([])
                snap["frameMs"] = round((time.perf_counter() - t0) * 1000, 1)
                return snap
            small = downscale(image, self.cfg.vision.process_width)
            self.last_full_frame, self.last_jpeg, self.last_capture = image, view_jpeg, now
            self.last_frame = small
            if self.detector_kind == "semantic":
                events = self._semantic_frame(image, small, now, untrusted)
                snap = self.snapshot(events)
                snap["frameMs"] = round((time.perf_counter() - t0) * 1000, 1)
                return snap
            scene = analyze_frame(small, self.vision_active, now)
            scene = suppress_static_stacks(scene, self.reference_scene, self.vision_active)
            motion = self.meter.update(small)
            # Camera settling / moving / a corner covered: show, but never commit.
            result = self.tracker.update(scene, float("inf") if untrusted else motion, now)
            result.motion = motion  # snapshots are strict JSON: the browser rejects Infinity
            events: list[Event] = []
            if result.new_stable is not None:
                self.reference_scene = result.new_stable
                events = self._on_stable(result.new_stable, view_jpeg)
            self.last_scene, self.last_tracker, self.last_frame = scene, result, small
            snap = self.snapshot(events)
        snap["frameMs"] = round((time.perf_counter() - t0) * 1000, 1)
        return snap

    def _invalidate_scan(self, message="Scene changed. Hold still and press Scan Objects."):
        self.scanner.invalidate()
        self.pending_check = None
        if self.setup_result is not None:
            self.setup_result_stale = True
        self.last_scene = None
        self.scan_status, self.scan_message = "idle", message

    def _semantic_frame(self, frame, small, now, untrusted: bool = False):
        motion = self.meter.update(small)
        if untrusted or motion > self.cfg.stability.motion_threshold:
            self.still_since, self.still_frames = now, 0
            self._invalidate_scan(self.mat_frame.message if untrusted else
                                  "Scene changed. Hold still and press Scan Objects.")
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
        if not self._scan_pose_ok():
            self._invalidate_scan("The camera moved during the scan. Hold still and scan again.")
            return []
        if result.request.purpose == "setup_check":
            return self._finish_setup_check(result)
        if result.scene is None:
            self.last_scene = None
            self.scan_status = "ambiguous" if result.ambiguous else "error"
            self.scan_message = result.error
            return []
        placed = self._in_workspace(result.scene, strict=True)
        if isinstance(placed, str):
            self.last_scene = None
            self.scan_status, self.scan_message = "ambiguous", placed
            return []
        result.scene = placed
        self.last_scene = self.reference_scene = result.scene
        self.scan_status = "valid"
        self.last_scan_purpose = "procedure"
        self.scan_message = "Scan accepted. After each move, hold still and press Scan Objects."
        return self._on_stable(result.scene, result.request.jpeg)

    def configure_detector(self, kind: str, labels: str = "") -> dict:
        with self.lock:
            try:
                if kind not in ("color", "semantic"):
                    raise ValueError("Unknown detector mode.")
                if kind == "color" and self.workspace == "setup":
                    raise ValueError("Setup Check uses semantic scans. Switch to Procedure mode before selecting Color.")
                if self.mode != "idle":
                    raise ValueError("Pause the current procedure before switching detectors or object descriptions.")
                if kind == "semantic":
                    if not self.cfg.semantic_beta:
                        raise ValueError("Semantic Objects beta is not enabled in server configuration.")
                    requested = parse_labels(labels)
                    if self.procedure and self.procedure.detector_kind == "semantic" and set(requested) != set(self.procedure.tracked_ids):
                        raise ValueError("Reset the saved semantic procedure before changing its objects.")
                    self.semantic_labels = requested
                    self._kick_semantic_preload()
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

    def _worker_state(self) -> str:
        return getattr(getattr(self.semantic_detector, "worker", None), "state", "unloaded")

    def _kick_semantic_preload(self) -> None:
        start = getattr(getattr(self.semantic_detector, "worker", None), "start_preload", None)
        if callable(start):
            start(on_done=self._notify_detector)

    def _notify_detector(self) -> None:
        cb = self.on_detector_change
        if cb is not None:
            cb()

    def _cmd_scan(self) -> list[Event]:
        if self.detector_kind != "semantic":
            self.notice = "Select Semantic Objects beta before scanning."
        elif self._worker_state() != "ready":
            self._kick_semantic_preload()
            self.notice = ("Loading model. Scan when it says Model ready." if self._worker_state() == "loading"
                           else "Model error. Retry loading, or use Color mode.")
        elif self.last_full_frame is None or not self._can_scan():
            self.notice = "Hold the table still before scanning."
        else:
            self.last_scene = None
            self.pending_check = None
            self.scan_pose = self._pose()
            self.scanner.submit(self.last_full_frame, self.last_jpeg, self.semantic_labels, self.last_capture)
            self.scan_status, self.scan_message = "scanning", "Scanning objects. Keep the table still."
            self.notice = ""
        return []

    def _can_scan(self):
        return (self.last_full_frame is not None and self.still_since is not None and self._mat_ok()
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
                "canScan": self.detector_kind == "semantic" and self._can_scan() and self._worker_state() == "ready",
                "workerMessage": getattr(getattr(self.semantic_detector, "worker", None), "public_message", "") or "",
                "switchLocked": self.mode != "idle",
                "procedureKind": self.procedure.detector_kind if self.procedure else None}

    def close(self):
        self.scanner.close()
        self.history_writer.close(timeout=self.cfg.history_close_timeout)

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
            if self.workspace == "setup" and action in PROCEDURE_ACTIONS:
                self.notice = "Switch to Procedure mode to use procedure controls."
                return self.snapshot()
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

    # -- mat stabilization ------------------------------------------------------------------------------

    def _stabilize(self, frame, jpeg: bytes, source: str, now: float):
        """Route one frame through mat tracking. Returns (image to evaluate, its JPEG, untrusted) or
        (None, None, True) when tracking failed and nothing may be evaluated."""
        self.frame_source = source
        self.last_raw_frame = frame
        mat = self.mat.process(source, frame, now)
        self.mat_frame = mat
        mode = (source, mat.bypass)
        if mode != self.mat_mode:  # different camera or coordinate system: start clean
            self.mat_mode = mode
            self.tracker.interrupt()
            self.reference_scene = None
            self.meter.reset()
        if mat.bypass:
            self.vision_active = self.cfg.vision
            return frame, jpeg, False
        if mat.canonical is None:
            self._mat_blocked(mat.message)
            return None, None, True
        cal = self.mat.calibration(source)
        self.vision_active = canonical_vision(self.cfg.vision, cal.canonical_size, self.cfg.mat.band_fraction)
        ok, buf = cv2.imencode(".jpg", mat.canonical, [cv2.IMWRITE_JPEG_QUALITY, 75])
        view = buf.tobytes() if ok else jpeg
        self.mat_view_jpeg, self.mat_view_seq = view, self.mat_view_seq + 1
        if not mat.trustworthy:
            self.tracker.interrupt()  # a settling window must start over once the camera is steady
        return mat.detect, view, not mat.trustworthy

    def _mat_blocked(self, message: str) -> None:
        """Tracking lost: forget the scene, stop any stability window and in-flight scan, stale old verdicts."""
        self.tracker.interrupt()
        self.meter.reset()
        self.last_scene = None
        self.last_tracker = TrackerResult("untracked", 0.0)
        self.still_since, self.still_frames = None, 0
        self._invalidate_scan(message)

    def _mat_active(self) -> bool:
        return self.mat_frame is not None and not self.mat_frame.bypass

    def _mat_ok(self) -> bool:
        return not self._mat_active() or self.mat_frame.trustworthy

    def _pose(self):
        if not self._mat_active() or self.mat_frame.track is None or self.mat_frame.track.corners is None:
            return None
        return (self.frame_source, self.mat.epoch(self.frame_source), self.mat_frame.track.corners.copy())

    def _scan_pose_ok(self) -> bool:
        """A scan result counts only if the mat stayed tracked, trusted and put since it was submitted."""
        submitted, current = self.scan_pose, self._pose()
        if submitted is None and current is None:
            return True
        if submitted is None or current is None or not self._mat_ok():
            return False
        source, epoch, corners = submitted
        if source != current[0] or epoch != current[1]:
            return False
        h, w = self.last_raw_frame.shape[:2]
        shift = float(np.max(np.linalg.norm(current[2] - corners, axis=1)))
        return shift <= self.cfg.mat.scan_max_shift_frac * float(np.hypot(w, h))

    def _in_workspace(self, scene: SceneState, strict: bool):
        """Canonical zones for semantic boxes, and ignore anything on the landmark band.
        strict (procedure scans): an ignored object makes the scan ambiguous instead of 'missing'."""
        if not self._mat_active():
            return scene
        band = self.cfg.mat.band_fraction
        kept = [o for o in scene.objects if in_workspace(o.center[0], o.center[1], band)]
        outside = [o for o in scene.objects if o not in kept]
        if outside and strict:
            names = ", ".join(o.id for o in outside)
            return f"{names} is on the edge band of the mat. Move it inside the workspace and scan again."
        assign_zones(kept, self.vision_active)
        return SceneState(objects=kept, captured_at=scene.captured_at)

    def calibrate_mat(self, points) -> dict:
        """Save the four clicked landmarks for the camera currently supplying frames."""
        with self.lock:
            if self.frame_source not in ("webcam", "phone"):
                self.notice = "The simulator does not need mat calibration."
            elif self.last_raw_frame is None:
                self.notice = "No camera picture yet. Wait for the video, then calibrate."
            else:
                try:
                    self.mat.calibrate(self.frame_source, self.last_raw_frame, points)
                except ValueError as exc:
                    self.notice = f"Mat not calibrated: {str(exc).splitlines()[0]}"
                else:
                    self.mat_mode = None  # next frame starts a clean stability window
                    self._invalidate_scan("Mat calibrated. Hold still, then scan.")
                    self.notice = "Mat calibrated. Hold still until it says Mat tracking."
            return self.snapshot()

    def clear_mat(self) -> dict:
        with self.lock:
            self.mat.clear(self.frame_source)
            self.mat_mode = None
            self._invalidate_scan()
            self.notice = "Mat calibration removed for this camera."
            return self.snapshot()

    def mat_status(self) -> dict:
        return {**self.mat.status(self.frame_source), "viewSeq": self.mat_view_seq}

    # -- Setup Check ----------------------------------------------------------------------------

    def set_workspace(self, workspace: str) -> dict:
        with self.lock:
            if workspace not in ("procedure", "setup") or workspace == self.workspace:
                return self.snapshot()
            if self.mode != "idle":
                self.notice = "Pause or finish the procedure before switching to Setup Check."
                return self.snapshot()
            if workspace == "setup":
                if not self.cfg.semantic_beta:
                    self.notice = "Setup Check needs the Semantic Objects beta (TEACHBACK_SEMANTIC_BETA=1)."
                    return self.snapshot()
                if self.detector_kind != "semantic":
                    self.configure_detector("semantic", ",".join(self.semantic_labels))
                    if self.detector_kind != "semantic":
                        return self.snapshot()
            self.workspace = workspace
            self._clear_setup_result()
            self._invalidate_scan("Hold the table still, then press Scan Objects.")
            self.notice = ("Setup Check: scan the organized table and capture it, or pick a saved setup and check it."
                           if workspace == "setup" else "Procedure mode. The saved procedure is unchanged.")
            return self.snapshot()

    def capture_setup(self, name: str) -> dict:
        with self.lock:
            storage = self.setups.status()
            if storage.state != "ready":
                self.notice = f"Setup not saved: {storage.message}"
                return self.snapshot()
            if not self._can_capture():
                self.notice = "Scan Objects first: capture needs an accepted scan of every described object."
                return self.snapshot()
            try:
                setup = SavedSetup.from_scene(name, self.last_scene, time.time())
            except ValueError as exc:  # pydantic's ValidationError is a ValueError
                self.notice = f"Setup not saved: {str(exc).splitlines()[0]}"
                return self.snapshot()
        # The save may be a database round trip (bounded by connect/statement timeouts). It runs outside
        # Session.lock so camera frames keep flowing meanwhile.
        try:
            self.setups.save(setup)
        except (SetupStorageError, ValueError, OSError) as exc:
            with self.lock:
                self.notice = f"Setup not saved: {str(exc).splitlines()[0]}"
                return self.snapshot()
        with self.lock:
            self.selected_setup_id = setup.id
            self._clear_setup_result()
            self.history_writer.request_refresh(setup.id)
            self.notice = f'Saved setup "{setup.name}" with {len(setup.objects)} objects.'
            return self.snapshot()

    def refresh_setups(self) -> dict:
        """Explicitly reload saved setups from storage (outside Session.lock; may be a database round trip)."""
        self.setups.refresh()
        with self.lock:
            if self.selected_setup_id and self.setups.get(self.selected_setup_id) is None:
                self.selected_setup_id = None
                self._clear_setup_result()
            self.notice = self.setups.status().message
            return self.snapshot()

    def select_setup(self, setup_id: str) -> dict:
        with self.lock:
            if self.setups.get(setup_id) is None:
                self.notice = "That saved setup is not available."
            else:
                self.selected_setup_id = setup_id
                self._clear_setup_result()
                self.pending_check = None
                self.history_writer.request_refresh(setup_id)
                self.notice = ""
            return self.snapshot()

    def check_setup(self) -> dict:
        with self.lock:
            setup = self.setups.get(self.selected_setup_id or "")
            if self.workspace != "setup" or self.detector_kind != "semantic":
                self.notice = "Switch to Setup Check first."
            elif setup is None:
                self.notice = "Select a saved setup to check."
            elif not self._can_scan():
                self.notice = "Hold the table still before checking."
            else:
                expected = [o.label for o in setup.objects]
                known = {label.casefold() for label in expected}
                # Also look for the other configured descriptions: that is how unexpected objects are found.
                vocabulary = tuple(expected + [label for label in self.semantic_labels if label.casefold() not in known])
                if len(vocabulary) > 6:
                    self.notice = "Too many descriptions to scan at once (max 6 including the setup's objects)."
                    return self.snapshot()
                self._clear_setup_result()
                self.last_scene = None
                self.pending_check = setup.id
                self.scan_pose = self._pose()
                self.scanner.submit(self.last_full_frame, self.last_jpeg, vocabulary, self.last_capture,
                                    purpose="setup_check")
                self.scan_status, self.scan_message = "scanning", f'Checking "{setup.name}". Keep the table still.'
                self.notice = ""
            return self.snapshot()

    def _finish_setup_check(self, result) -> list[Event]:
        setup_id, self.pending_check = self.pending_check, None
        setup = self.setups.get(setup_id or "")
        if result.scene is None:
            # A failed or ambiguous scan never yields a verdict, least of all a passing one.
            self.scan_status = "ambiguous" if result.ambiguous else "error"
            self.scan_message = f"Setup not checked: {result.error}"
            return []
        placed = self._in_workspace(result.scene, strict=False)  # objects on the landmark band are ignored
        result.scene = placed
        requested = {label.casefold() for label in result.request.labels}
        expected = {o.label.casefold() for o in setup.objects} if setup else set()
        if setup is None or setup_id != self.selected_setup_id or not expected <= requested:
            self.scan_status, self.scan_message = "idle", "The selected setup changed. Press Check Setup again."
            return []
        self.setup_result = check_setup(setup, result.scene, result.scene.captured_at)
        self.setup_result_stale = False
        # One accepted verdict -> one event_id. This is the only place events are created, and a scan
        # result is delivered once, so re-sent frames and snapshots never add history. submit() never
        # blocks: the verdict below is final whether or not the write later succeeds.
        self.setup_event = SetupCheckEvent.from_result(self.setup_result)
        self.history_writer.submit(self.setup_event)
        self.last_scene = result.scene
        self.scan_status, self.last_scan_purpose = "valid", "setup_check"
        self.scan_message = "Setup checked. Fix anything listed, hold still, and check again."
        r = self.setup_result
        if r.status == "complete":
            return [speak(f"{setup.name} is complete and correctly arranged.", "success")]
        findings = ((r.missing, "missing"), (r.unexpected, "unexpected"), (r.misplaced, "in the wrong zone"))
        parts = [f"{len(items)} {word}" for items, word in findings if items]
        return [speak(f"{setup.name} needs attention: " + ", ".join(parts) + ".", "error")]

    def _can_capture(self) -> bool:
        return (self.workspace == "setup" and self.detector_kind == "semantic" and self.scan_status == "valid"
                and self.last_scan_purpose == "procedure" and self.last_scene is not None
                and bool(self.last_scene.objects))

    def _clear_setup_result(self) -> None:
        self.setup_result, self.setup_result_stale = None, False
        self.setup_event = None

    def refresh_history(self) -> dict:
        """Explicit Refresh: re-verify the hypertable and reload the selected setup's history (background)."""
        with self.lock:
            queued = self.history_writer.request_refresh(None)
            if self.selected_setup_id:
                self.history_writer.request_refresh(self.selected_setup_id)
            state = self.history.status().state
            self.notice = ("History is disabled without Tiger Data." if state == "disabled"
                           else "Refreshing history…" if queued else "History refresh already queued.")
            return self.snapshot()

    def history_status(self) -> dict:
        """Cache-only view for snapshots and /api/health: no database access on this hot path."""
        sid = self.selected_setup_id
        summary = self.history.summary(sid) if sid else None
        return {
            **self.history.status().to_json(),
            "recent": [e.to_json() for e in self.history.recent(sid)] if sid else [],
            "summary": summary.to_json() if summary else None,
            "writer": self.history_writer.stats(),
            "errors": list(self.history.errors),
        }

    def setup_status(self) -> dict:
        setups = self.setups.list()
        selected = next((s for s in setups if s.id == self.selected_setup_id), None)
        return {
            "available": self.cfg.semantic_beta,
            "setups": [{"id": s.id, "name": s.name, "objectCount": len(s.objects)} for s in setups],
            "selected": selected.to_json() if selected else None,
            "storage": self.setups.status().to_json(),
            "canCapture": self._can_capture() and self.setups.status().state == "ready",
            "canCheck": self.workspace == "setup" and selected is not None and self._can_scan(),
            "checking": self.pending_check is not None,
            "result": self.setup_result.to_json() if self.setup_result else None,
            "resultStale": self.setup_result_stale,
            "repositoryErrors": list(self.setups.errors),
            "history": self.history_status(),
            "resultHistory": {"eventId": str(self.setup_event.event_id),
                              "state": self.history_writer.event_state(self.setup_event.event_id)}
            if self.setup_event else None,
        }

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
        with self.lock:
            return self._snapshot(events)

    def _snapshot(self, events: list[Event] | None = None) -> dict:
        proc = self.current_procedure()
        return {
            "type": "update",
            "mode": self.mode,
            "workspace": self.workspace,
            "setup": self.setup_status(),
            "detector": self.detector_status(),
            "notice": self.notice,
            "scene": self.last_scene.to_json() if self.last_scene else None,
            "zones": [
                {"id": z.id, "label": z.label, "x": z.x, "y": z.y, "w": z.w, "h": z.h}
                for z in self.vision_active.zones
            ],
            "mat": self.mat_status(),
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
