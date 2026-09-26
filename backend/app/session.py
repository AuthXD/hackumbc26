"""The single live demo session: mode switching, frame processing, keyframes, persistence."""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Literal

from .config import DATA_DIR, Settings, settings
from .engine import Event, PracticeEngine, TeachRecorder, speak
from .models import Procedure, SceneState, StepText
from .stability import StabilityTracker, TrackerResult
from .vision import MotionMeter, analyze_frame, decode_jpeg, downscale, suppress_static_stacks

Mode = Literal["idle", "teaching", "practicing"]

PROCEDURE_FILE = DATA_DIR / "procedure.json"
KEYFRAME_DIR = DATA_DIR / "keyframes"


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
        self.last_tracker: TrackerResult | None = None
        self.reference_scene: SceneState | None = None  # last committed stable state
        self.notice = ""  # one-line feedback for the last command
        self.pending_ai: list[int] = []  # learned step indexes awaiting an AI description
        if persist:
            self._load()

    # -- frames ---------------------------------------------------------------------------------

    def process_frame(self, jpeg: bytes, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        t0 = time.perf_counter()
        frame = decode_jpeg(jpeg)
        if frame is None:
            return {"type": "update", "error": "Could not decode frame"}
        small = downscale(frame, self.cfg.vision.process_width)
        scene = analyze_frame(small, self.cfg.vision, now)
        with self.lock:
            scene = suppress_static_stacks(scene, self.reference_scene, self.cfg.vision)
            motion = self.meter.update(small)
            result = self.tracker.update(scene, motion, now)
            events: list[Event] = []
            if result.new_stable is not None:
                self.reference_scene = result.new_stable
                events = self._on_stable(result.new_stable, jpeg)
            self.last_scene, self.last_tracker = scene, result
            snap = self.snapshot(events)
        snap["frameMs"] = round((time.perf_counter() - t0) * 1000, 1)
        return snap

    def _on_stable(self, scene: SceneState, jpeg: bytes) -> list[Event]:
        if self.mode != "idle":
            self.notice = ""  # the status card takes over from the command's one-liner
        if self.mode == "teaching" and self.recorder:
            image = self._store_keyframe(jpeg)
            events = self.recorder.on_stable(scene, image)
            if self.recorder.steps and self.tracker.required is None:
                # From the first learned step on, a missing object means occlusion.
                self.tracker.required = self.recorder.tracked_set
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
            }.get(action)
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

    def set_ai_description(self, index: int, text: StepText) -> None:
        with self.lock:
            proc = self.current_procedure()
            if proc and index < len(proc.steps):
                proc.steps[index].ai_description = text
                if self.procedure is proc or self.mode != "teaching":
                    self._save()

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
        "trackedIds": proc.tracked_ids,
        "steps": [
            s.model_dump(by_alias=True, mode="json", exclude={"before_state", "after_state"}) for s in proc.steps
        ],
    }
