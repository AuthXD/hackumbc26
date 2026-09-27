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
class MatConfig:
    """Mat calibration + tracking. Pixel values refer to the working image (long side = work_long_side)."""

    canonical_long_side: int = 960  # top-down mat image size (long side, px)
    band_fraction: float = 0.10  # outer band holding the corner stickers; never part of the workspace
    work_long_side: int = 960  # tracking resolution
    patch_radius_frac: float = 0.06  # landmark patch radius, fraction of the working image's short side
    points_per_corner: int = 30
    min_corner_features: int = 6  # a landmark needs this much texture to be calibrated
    min_feature_strength: float = 0.002  # min eigenvalue; real stickers measured 0.025-0.064, blank mat 0
    orb_features: int = 2000
    # calibration geometry
    min_quad_area_frac: float = 0.04
    min_edge_frac: float = 0.05
    min_angle_deg: float = 35.0
    max_angle_deg: float = 145.0
    max_opposite_ratio: float = 3.0
    # tracking validation (fail closed)
    min_inliers: int = 12
    min_corner_inliers: int = 3  # a landmark counts as visible with this many inlier points
    min_corner_ncc: float = 0.5  # ...and when its patch still looks like the calibration patch
    max_missing_corner_s: float = 1.0  # one covered landmark is tolerated this long
    max_reproj_px: float = 2.5
    max_fb_error: float = 1.5  # Lucas-Kanade forward-backward consistency, px
    corner_margin_frac: float = 0.0  # tracked corners must stay inside the frame
    max_jump_frac: float = 0.06  # corner jump between frames (fraction of the diagonal) = abrupt motion
    max_area_change: float = 0.2
    steady_px: float = 2.0  # corner motion per frame below this counts as "held still"
    steady_frames: int = 3  # consecutive steady frames before verdicts are allowed
    blur_ratio: float = 0.35  # landmark sharpness vs calibration below this = motion blur
    stale_s: float = 1.0  # a transform older than this is never reused
    smooth_px: float = 0.8  # jitter below this is smoothed; larger movement snaps immediately
    smooth_alpha: float = 0.5
    scan_max_shift_frac: float = 0.01  # camera shift during a scan (fraction of diagonal) that voids it


@dataclass
class ProcedureConfig:
    steps_per_procedure: int = 4
    min_objects: int = 2  # objects that must be visible to capture a starting layout


SEMANTIC_MAX_DIM_RANGE = (224, 1280)  # below: objects vanish; above: slower than the 960 px canonical mat


def _semantic_max_dim() -> int | str:
    return os.getenv("TEACHBACK_SEMANTIC_MAX_DIM", "").strip() or 640


def validate_semantic_max_dim(value: object) -> int:
    """The longest image edge sent to LocateAnything. Rejects anything that is not a whole number of pixels
    in SEMANTIC_MAX_DIM_RANGE instead of silently clamping it."""
    low, high = SEMANTIC_MAX_DIM_RANGE
    text = str(value).strip()
    if isinstance(value, bool) or not (text.isascii() and text.isdigit()) or not low <= int(text) <= high:
        raise ValueError(f"TEACHBACK_SEMANTIC_MAX_DIM must be a whole number of pixels from {low} to {high}; "
                         f"got {text[:20]!r}.")
    return int(text)


@dataclass
class Settings:
    vision: VisionConfig = field(default_factory=VisionConfig)
    stability: StabilityConfig = field(default_factory=StabilityConfig)
    procedure: ProcedureConfig = field(default_factory=ProcedureConfig)
    mat: MatConfig = field(default_factory=MatConfig)
    # Longest edge of the image sent to LocateAnything (the canonical mat when tracking). Measured in
    # benchmarks/locate_anything/evidence/size-sweep.md.
    semantic_max_dim: int = field(default_factory=_semantic_max_dim)
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
    # Tiger Cloud (PostgreSQL) for saved setups. Secret: repr=False keeps it out of any logged Settings.
    tiger_database_url: str = field(default_factory=lambda: os.getenv("TIGER_DATABASE_URL", "").strip(), repr=False)
    tiger_connect_timeout: int = 5  # seconds, per connection attempt
    tiger_statement_timeout_ms: int = 5000  # per transaction
    history_queue_size: int = 32  # bounded; overflow is reported, never silent
    history_close_timeout: float = 5.0  # seconds to drain accepted events at shutdown
    # Trusted HTTPS tunnel used by the phone-camera QR. LAN HTTP is still shown as a view-only fallback.
    phone_public_url: str = field(default_factory=lambda: os.getenv("TEACHBACK_PHONE_URL", "").strip())

    def __post_init__(self) -> None:
        self.semantic_max_dim = validate_semantic_max_dim(self.semantic_max_dim)


settings = Settings()
