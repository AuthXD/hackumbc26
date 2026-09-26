"""HSV color segmentation → SceneObjects with zones and stacking.

Deliberately simple: one object per configured color (the largest blob of that color),
zone = the zone rectangle containing the object's center, and stacking inferred from
bounding-box overlap.
"""

from __future__ import annotations

import cv2
import numpy as np

from .config import ColorRange, VisionConfig
from .models import SceneObject, SceneState

BOTTOM_EDGE_TOLERANCE = 0.02  # normalized; below this, "which box sits lower" is a tie


def decode_jpeg(data: bytes) -> np.ndarray | None:
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def downscale(frame: np.ndarray, width: int) -> np.ndarray:
    h, w = frame.shape[:2]
    if w <= width:
        return frame
    return cv2.resize(frame, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)


def color_mask(hsv: np.ndarray, bands) -> np.ndarray:
    mask = np.zeros(hsv.shape[:2], np.uint8)
    for lo, hi in bands:
        mask |= cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    return mask


def detect_objects(frame_bgr: np.ndarray, cfg: VisionConfig) -> list[SceneObject]:
    small = downscale(frame_bgr, cfg.process_width)
    H, W = small.shape[:2]
    blurred = cv2.GaussianBlur(small, (cfg.blur_kernel, cfg.blur_kernel), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
    kernel = np.ones((cfg.morph_kernel, cfg.morph_kernel), np.uint8)
    min_area = cfg.min_area_frac * W * H

    objects: list[SceneObject] = []
    for color in cfg.colors:
        mask = color_mask(hsv, color.bands)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        m = cv2.moments(contour)
        cx, cy = (m["m10"] / m["m00"], m["m01"] / m["m00"]) if m["m00"] else (x + w / 2, y + h / 2)
        hull_area = cv2.contourArea(cv2.convexHull(contour)) or area
        solidity = area / hull_area
        size_score = min(1.0, area / (4 * min_area))
        objects.append(
            SceneObject(
                id=color.name,
                color=color.name,
                center=(round(cx / W, 4), round(cy / H, 4)),
                bbox=(round(x / W, 4), round(y / H, 4), round(w / W, 4), round(h / H, 4)),
                zone=None,
                confidence=round(0.6 * solidity + 0.4 * size_score, 2),
            )
        )

    infer_stacking(objects, cfg)
    assign_zones(objects, cfg)
    return objects


def _overlap_ratio(a: SceneObject, b: SceneObject) -> float:
    ax, ay, aw, ah = a.bbox
    bx, by, bw, bh = b.bbox
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    smaller = min(aw * ah, bw * bh)
    return (ix * iy) / smaller if smaller > 0 else 0.0


def _bottom(o: SceneObject) -> float:
    return o.bbox[1] + o.bbox[3]


def _is_above(a: SceneObject, b: SceneObject) -> bool:
    """Is `a` physically on top of `b`, given their boxes overlap?

    Angled camera: the top object's base edge sits higher in the image than the lower object's.
    Overhead camera with a small object on a larger one: the smaller box's bottom edge is inside the
    larger box, so the same rule holds. On a tie, the smaller visible box is the one on top.
    """
    ba, bb = _bottom(a), _bottom(b)
    if abs(ba - bb) > BOTTOM_EDGE_TOLERANCE:
        return ba < bb
    return a.bbox[2] * a.bbox[3] < b.bbox[2] * b.bbox[3]


def _rests_on(a: SceneObject, b: SceneObject, cfg: VisionConfig) -> bool:
    if _overlap_ratio(a, b) >= cfg.stack_overlap_ratio:
        return _is_above(a, b)
    # Angled camera: the top object hides the lower one's top face, so the two boxes only touch.
    # Treat "a's bottom edge sits on b's upper edge, mostly over b" as stacked.
    ax, ay, aw, ah = a.bbox
    bx, by, bw, bh = b.bbox
    h_overlap = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    if h_overlap < cfg.stack_min_width_overlap * min(aw, bw):
        return False
    a_bottom = ay + ah
    return ay < by and by - cfg.stack_touch_tolerance <= a_bottom <= by + 0.5 * bh


def infer_stacking(objects: list[SceneObject], cfg: VisionConfig) -> None:
    for a in objects:
        below = [b for b in objects if b is not a and _rests_on(a, b, cfg)]
        # In a tower, the object directly underneath is the highest of the candidates below.
        a.stacked_on = min(below, key=_bottom).id if below else None


def assign_zones(objects: list[SceneObject], cfg: VisionConfig) -> None:
    by_id = {o.id: o for o in objects}
    for o in objects:
        o.zone = next((z.id for z in cfg.zones if z.contains(*o.center)), None)
    # A stacked object is in whatever zone its base is in (follow the tower down).
    for o in objects:
        base, seen = o, {o.id}
        while base.stacked_on and base.stacked_on in by_id and base.stacked_on not in seen:
            base = by_id[base.stacked_on]
            seen.add(base.id)
        o.zone = base.zone


def sample_hsv(frame_bgr: np.ndarray, x: float, y: float, radius: int = 4) -> tuple[int, int, int]:
    """Median HSV of a small patch around normalized point (x, y)."""
    H, W = frame_bgr.shape[:2]
    cx, cy = int(x * (W - 1)), int(y * (H - 1))
    patch = frame_bgr[max(0, cy - radius): cy + radius + 1, max(0, cx - radius): cx + radius + 1]
    hsv = cv2.cvtColor(cv2.GaussianBlur(patch, (3, 3), 0), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    h = hsv[:, 0].astype(int)
    # Hue is circular (0 and 179 are both red): take the median after rotating away from the seam.
    if np.ptp(h) > 90:
        h = (h + 90) % 180
        med_h = (int(np.median(h)) - 90) % 180
    else:
        med_h = int(np.median(h))
    return med_h, int(np.median(hsv[:, 1])), int(np.median(hsv[:, 2]))


def color_from_sample(name: str, display: str, hsv: tuple[int, int, int], hue_tol: int = 9) -> ColorRange:
    """HSV band(s) centered on a sampled pixel; splits the band when it crosses hue 0/179."""
    h, s, v = hsv
    lo_s, lo_v = max(60, int(s * 0.55)), max(40, int(v * 0.45))
    lo_h, hi_h = h - hue_tol, h + hue_tol
    if lo_h < 0:
        bands = [((0, lo_s, lo_v), (hi_h, 255, 255)), ((180 + lo_h, lo_s, lo_v), (179, 255, 255))]
    elif hi_h > 179:
        bands = [((lo_h, lo_s, lo_v), (179, 255, 255)), ((0, lo_s, lo_v), (hi_h - 180, 255, 255))]
    else:
        bands = [((lo_h, lo_s, lo_v), (hi_h, 255, 255))]
    return ColorRange(name, bands, display)


def suppress_static_stacks(scene: SceneState, reference: SceneState | None, cfg: VisionConfig) -> SceneState:
    """An object that has not moved since the last committed state cannot have *become* stacked.

    In a single 2D image "resting on top of" and "touching from behind" look the same, so a new tower
    built next to a stationary object could make that object look stacked. Physically, stacking an
    object requires moving it, so new stack relations on objects that stayed put are dropped.
    """
    if reference is None:
        return scene
    ref = {o.id: o for o in reference.objects if o.visible}
    changed = False
    for o in scene.objects:
        r = ref.get(o.id)
        if not o.stacked_on or r is None or r.stacked_on == o.stacked_on:
            continue
        moved = abs(o.center[0] - r.center[0]) + abs(o.center[1] - r.center[1])
        if moved < cfg.static_move_threshold:
            o.stacked_on = r.stacked_on  # keep whatever relation it had when it last settled
            changed = True
    if changed:
        assign_zones(scene.objects, cfg)
    return scene


class MotionMeter:
    """Percentage of pixels (0-100) whose brightness changed noticeably since the previous frame.

    A changed-pixel fraction, unlike a mean difference, is not diluted when a hand covers only a
    small part of the frame.
    """

    def __init__(self, pixel_threshold: int = 15) -> None:
        self._prev: np.ndarray | None = None
        self.pixel_threshold = pixel_threshold

    def update(self, frame_bgr: np.ndarray) -> float:
        gray = cv2.cvtColor(cv2.resize(frame_bgr, (96, 72), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        prev, self._prev = self._prev, gray
        if prev is None:
            return 0.0
        changed = cv2.absdiff(gray, prev) > self.pixel_threshold
        return float(np.count_nonzero(changed) * 100.0 / changed.size)

    def reset(self) -> None:
        self._prev = None


def analyze_frame(frame_bgr: np.ndarray, cfg: VisionConfig, now: float) -> SceneState:
    return SceneState(objects=detect_objects(frame_bgr, cfg), captured_at=now)
