"""All tunable thresholds live here so the demo can be adjusted on-site in one place.

Coordinates are normalized to the camera frame: (0, 0) is top-left, (1, 1) is bottom-right.
Hue follows OpenCV's convention (0-179).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

# Learned procedure, keyframes, and color calibration live here (tests point it at a temp dir).
DATA_DIR = Path(os.getenv("TEACHBACK_DATA_DIR") or BACKEND_ROOT / "data")


@dataclass
class ColorRange:
    """One or more HSV (lower, upper) bands that together define a color. Red wraps around hue 0."""

    name: str
    bands: list[tuple[tuple[int, int, int], tuple[int, int, int]]]
    display: str  # CSS color used for overlays in the UI


@dataclass
class Zone:
    id: str
    label: str
    x: float
    y: float
    w: float
    h: float

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h


def default_colors() -> list[ColorRange]:
    return [
        ColorRange("red", [((0, 120, 70), (8, 255, 255)), ((168, 120, 70), (179, 255, 255))], "#ef4444"),
        ColorRange("yellow", [((18, 110, 110), (34, 255, 255))], "#facc15"),
        ColorRange("green", [((38, 70, 45), (85, 255, 255))], "#22c55e"),
        ColorRange("blue", [((92, 110, 45), (130, 255, 255))], "#3b82f6"),
    ]


def default_zones() -> list[Zone]:
    # Three side-by-side columns with gaps, so an object is rarely ambiguous between zones.
    return [
        Zone("A", "Zone A", 0.03, 0.12, 0.29, 0.80),
        Zone("B", "Zone B", 0.355, 0.12, 0.29, 0.80),
        Zone("C", "Zone C", 0.68, 0.12, 0.29, 0.80),
    ]


@dataclass
class VisionConfig:
    process_width: int = 320  # frames are downscaled to this width before segmentation
    min_area_frac: float = 0.002  # smallest blob (fraction of frame) that counts as an object
    blur_kernel: int = 5
    morph_kernel: int = 5
    # Stacking: two boxes overlap by at least this fraction of the smaller box.
    stack_overlap_ratio: float = 0.30
    # Angled camera: boxes that merely touch count as stacked when the upper one's bottom edge is
    # within this distance of the lower one's top edge and they share this much width.
    stack_touch_tolerance: float = 0.015
    stack_min_width_overlap: float = 0.6
    # An object whose center moved less than this (normalized, |dx|+|dy|) since the last committed
    # state is "static" and cannot newly become stacked.
    static_move_threshold: float = 0.04
    colors: list[ColorRange] = field(default_factory=default_colors)
    zones: list[Zone] = field(default_factory=default_zones)


@dataclass
class StabilityConfig:
    stable_ms: float = 700.0  # arrangement must stay unchanged this long
    min_frames: int = 3  # ...and across at least this many frames
    # Percent of pixels that changed since the previous frame. Above this the scene is considered
    # "moving" (a hand is working) and nothing is committed.
    motion_threshold: float = 1.5
    motion_gate_enabled: bool = True


@dataclass
class ProcedureConfig:
    steps_per_procedure: int = 4
    min_objects: int = 2  # objects that must be visible to capture a starting layout


@dataclass
class Settings:
    vision: VisionConfig = field(default_factory=VisionConfig)
    stability: StabilityConfig = field(default_factory=StabilityConfig)
    procedure: ProcedureConfig = field(default_factory=ProcedureConfig)
    semantic_beta: bool = field(default_factory=lambda: os.getenv("TEACHBACK_SEMANTIC_BETA") == "1")
    locate_distro: str = field(default_factory=lambda: os.getenv("LOCATE_WSL_DISTRO", "Ubuntu"))
    locate_model: str = field(default_factory=lambda: os.getenv("LOCATE_MODEL", "/home/authxd/models/locate-anything-q6_k.gguf"))
    locate_library: str = field(default_factory=lambda: os.getenv("LOCATE_LIBRARY", str(REPO_ROOT / "benchmarks/locate_anything/build/liblocate_anything.so")))
    locate_startup_timeout: float = 120.0
    locate_request_timeout: float = 10.0
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", "").strip())
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL") or "gemini-3.5-flash".strip())
    elevenlabs_api_key: str = field(default_factory=lambda: os.getenv("ELEVENLABS_API_KEY", "").strip())
    elevenlabs_voice_id: str = field(
        default_factory=lambda: os.getenv("ELEVENLABS_VOICE_ID") or "JBFqnCBsd6RMkjVDRZzb".strip()
    )
    elevenlabs_model: str = field(default_factory=lambda: os.getenv("ELEVENLABS_MODEL") or "eleven_flash_v2_5".strip())


settings = Settings()
