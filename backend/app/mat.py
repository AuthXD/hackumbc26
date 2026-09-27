"""Mat calibration and tracking: evaluate objects in mat coordinates even when the camera moves a little.

    camera frame -> track the four calibrated corner landmarks -> validate -> homography
                 -> perspective-warp to a fixed top-down "canonical" mat image
                 -> exclude the outer landmark band -> detectors + Zones A/B/C in canonical coordinates

Calibration is explicit: the user clicks the four landmarks in order (TL creature, TR frog, BR potion,
BL logo). The custom stickers are not fiducials, so tracking is anchored to image patches captured at
calibration time: feature points inside each landmark patch are tracked with pyramidal Lucas-Kanade,
re-anchored against the reference image every frame (no drift), fitted with a RANSAC homography, and
re-acquired with ORB after loss. Every frame is validated; anything doubtful fails closed.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from pydantic import Field, field_validator, model_validator

from .config import MatConfig, VisionConfig, Zone
from .models import CamelModel

LANDMARKS = ("top-left creature", "top-right frog", "bottom-right potion", "bottom-left SteelSeries logo")
Source = Literal["webcam", "phone"]


# -- geometry -------------------------------------------------------------------------------------------

def _cross(o: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    return float((a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]))


def quad_problem(points_px: np.ndarray, frame_w: float, frame_h: float, cfg: MatConfig) -> str | None:
    """Why four TL, TR, BR, BL points are not a usable mat outline, or None if they are.

    Requires a convex, clockwise (on screen) quadrilateral with enough area, no near-collinear corners,
    no wild distortion, and every point inside the frame.
    """
    p = np.asarray(points_px, dtype=np.float64).reshape(-1, 2)
    if p.shape != (4, 2) or not np.all(np.isfinite(p)):
        return "Select exactly four points."
    if np.any(p[:, 0] < 0) or np.any(p[:, 0] > frame_w) or np.any(p[:, 1] < 0) or np.any(p[:, 1] > frame_h):
        return "Every corner must be inside the camera view."
    diag = math.hypot(frame_w, frame_h)
    edges = [np.linalg.norm(p[(i + 1) % 4] - p[i]) for i in range(4)]
    if min(edges) < cfg.min_edge_frac * diag:
        return "Two corners are too close together. Select the four corner stickers."
    crosses = [_cross(p[i], p[(i + 1) % 4], p[(i + 2) % 4]) for i in range(4)]
    if all(c < 0 for c in crosses):
        return "Wrong order. Select clockwise: creature, frog, potion, logo."
    if not all(c > 0 for c in crosses):
        return "The corners cross or fold over. Select them in order around the mat."
    area = 0.5 * abs(sum(p[i][0] * p[(i + 1) % 4][1] - p[(i + 1) % 4][0] * p[i][1] for i in range(4)))
    if area < cfg.min_quad_area_frac * frame_w * frame_h:
        return "The mat is too small in the picture. Move the camera closer."
    for i in range(4):
        a, b, c = p[i - 1], p[i], p[(i + 1) % 4]
        v1, v2 = a - b, c - b
        cos = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))
        angle = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
        if not cfg.min_angle_deg <= angle <= cfg.max_angle_deg:
            return "The outline is too skewed. Check the corner order or face the camera at the mat."
    if max(edges[0], edges[2]) / min(edges[0], edges[2]) > cfg.max_opposite_ratio or \
            max(edges[1], edges[3]) / min(edges[1], edges[3]) > cfg.max_opposite_ratio:
        return "The outline is too distorted. Point the camera more directly at the mat."
    return None


def canonical_size(points_px: np.ndarray, long_side: int) -> tuple[int, int]:
    """Top-down mat image size that keeps the landmark rectangle's measured aspect."""
    p = np.asarray(points_px, dtype=np.float64)
    width = (np.linalg.norm(p[1] - p[0]) + np.linalg.norm(p[2] - p[3])) / 2
    height = (np.linalg.norm(p[3] - p[0]) + np.linalg.norm(p[2] - p[1])) / 2
    scale = long_side / max(width, height)
    return max(8, round(width * scale)), max(8, round(height * scale))


def canonical_corners(size: tuple[int, int]) -> np.ndarray:
    w, h = size
    return np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)


def homography_to_canonical(corners_px: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """3x3 matrix mapping source-frame pixels (landmarks at the corners) onto the canonical image."""
    return cv2.getPerspectiveTransform(np.asarray(corners_px, np.float32), canonical_corners(size))


def canonical_zones(size: tuple[int, int], band: float, gap: float = 0.02) -> list[Zone]:
    """Zones A/B/C inside the usable workspace (the band is excluded), split along the mat's long axis."""
    w, h = size
    inner = 1 - 2 * band
    third = (inner - 2 * gap) / 3
    zones = []
    for i, zid in enumerate("ABC"):
        start = band + i * (third + gap)
        if h >= w:  # portrait mat: A at the top, C at the bottom
            zones.append(Zone(zid, f"Zone {zid}", band, start, inner, third))
        else:
            zones.append(Zone(zid, f"Zone {zid}", start, band, third, inner))
    return zones


def canonical_vision(vision: VisionConfig, size: tuple[int, int], band: float) -> VisionConfig:
    return replace(vision, zones=canonical_zones(size, band))


def in_workspace(x: float, y: float, band: float) -> bool:
    """Is a normalized canonical point inside the usable area (outside the landmark band)?"""
    return band <= x <= 1 - band and band <= y <= 1 - band


def mask_band(canonical: np.ndarray, band: float) -> np.ndarray:
    """Black out the outer landmark band so nothing placed on a sticker can be detected."""
    out = canonical.copy()
    h, w = out.shape[:2]
    bx, by = round(w * band), round(h * band)
    out[:by] = 0
    out[h - by:] = 0
    out[:, :bx] = 0
    out[:, w - bx:] = 0
    return out


def to_work(frame: np.ndarray, long_side: int) -> tuple[np.ndarray, float]:
    """Grayscale working image (long side <= long_side) and its scale relative to the frame."""
    h, w = frame.shape[:2]
    scale = min(1.0, long_side / max(w, h))
    small = cv2.resize(frame, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else frame
    gray = small if small.ndim == 2 else cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return gray, scale


def sharpness(gray: np.ndarray, centers: np.ndarray, radius: int) -> float:
    """Median variance-of-Laplacian over the landmark patches (low = motion blur / defocus)."""
    values = []
    h, w = gray.shape[:2]
    for cx, cy in centers:
        x0, y0 = int(max(0, cx - radius)), int(max(0, cy - radius))
        x1, y1 = int(min(w, cx + radius)), int(min(h, cy + radius))
        if x1 - x0 > 4 and y1 - y0 > 4:
            values.append(float(cv2.Laplacian(gray[y0:y1, x0:x1], cv2.CV_64F).var()))
    return float(np.median(values)) if values else 0.0


# -- calibration ----------------------------------------------------------------------------------------

class MatCalibration(CamelModel):
    """Four clicked landmarks, normalized to the calibrated frame, plus the canonical layout."""

    version: Literal[1] = 1
    source: Source
    frame_width: int = Field(gt=0)
    frame_height: int = Field(gt=0)
    points: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]
    canonical_width: int = Field(gt=0)
    canonical_height: int = Field(gt=0)
    created_at: float

    @field_validator("points")
    @classmethod
    def normalized(cls, v):
        if not all(0 <= c <= 1 and math.isfinite(c) for pt in v for c in pt):
            raise ValueError("points must be normalized to the frame")
        return v

    @model_validator(mode="after")
    def plausible(self):
        problem = quad_problem(self.points_px(self.frame_width, self.frame_height),
                               self.frame_width, self.frame_height, MatConfig())
        if problem:
            raise ValueError(problem)
        return self

    def points_px(self, width: float, height: float) -> np.ndarray:
        return np.array(self.points, dtype=np.float64) * [width, height]

    @property
    def aspect(self) -> float:
        return self.frame_width / self.frame_height

    @property
    def canonical_size(self) -> tuple[int, int]:
        return self.canonical_width, self.canonical_height


def build_calibration(source: Source, frame: np.ndarray, points_norm, cfg: MatConfig,
                      now: float | None = None) -> MatCalibration:
    """Validate clicked points against the frame; raises ValueError with a user-facing reason."""
    h, w = frame.shape[:2]
    pts = np.asarray(points_norm, dtype=np.float64).reshape(-1, 2)
    if pts.shape != (4, 2):
        raise ValueError("Select exactly four points.")
    px = pts * [w, h]
    problem = quad_problem(px, w, h, cfg)
    if problem:
        raise ValueError(problem)
    cw, ch = canonical_size(px, cfg.canonical_long_side)
    return MatCalibration(source=source, frame_width=w, frame_height=h,
                          points=tuple((float(x), float(y)) for x, y in pts),
                          canonical_width=cw, canonical_height=ch, created_at=now or time.time())


# -- tracking -------------------------------------------------------------------------------------------

TrackState = Literal["tracking", "unsteady", "lost"]


@dataclass
class TrackResult:
    state: TrackState
    message: str
    trustworthy: bool = False  # safe to update Teach/Practice/Setup Check from this frame
    corners: np.ndarray | None = None  # tracked TL,TR,BR,BL in source-frame pixels
    homography: np.ndarray | None = None  # source-frame pixels -> canonical pixels
    inliers: int = 0
    reproj_error: float | None = None
    motion: float | None = None  # corner movement since the previous frame, working-image pixels
    corners_seen: int = 0
    epoch: int = 0  # increments whenever tracking is lost; scans compare it
    frame_size: tuple[int, int] | None = None  # (width, height) of the frame the corners refer to


@dataclass
class _Anchors:
    ref_points: np.ndarray  # (N, 2) feature points in the reference working image
    corner_of: np.ndarray  # (N,) which landmark each point belongs to


class MatTracker:
    """Track the calibrated mat in one camera stream. Owns no global state; one per calibration."""

    def __init__(self, calibration: MatCalibration, reference: np.ndarray, cfg: MatConfig) -> None:
        self.cal = calibration
        self.cfg = cfg
        self.ref = reference if reference.ndim == 2 else cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
        rh, rw = self.ref.shape[:2]
        self.ref_scale = rw / calibration.frame_width
        self.ref_corners = calibration.points_px(rw, rh).astype(np.float32)
        self.radius = max(6, round(cfg.patch_radius_frac * min(rw, rh)))
        self.anchors = self._anchor_points()
        self.ref_sharpness = sharpness(self.ref, self.ref_corners, self.radius)
        self.ref_global_sharpness = float(cv2.Laplacian(self.ref, cv2.CV_64F).var())
        self.ref_patches = [self._patch(self.ref, c) for c in self.ref_corners]
        self.orb = cv2.ORB_create(nfeatures=cfg.orb_features, fastThreshold=7, edgeThreshold=15, patchSize=15)
        mask = np.zeros_like(self.ref)
        for cx, cy in self.ref_corners:
            cv2.circle(mask, (int(cx), int(cy)), int(self.radius * 1.5), 255, -1)
        self.ref_kp, self.ref_desc = self.orb.detectAndCompute(self.ref, mask)
        self.epoch = 0
        self._reset()

    # -- setup ------------------------------------------------------------------------------------------

    def _anchor_points(self) -> _Anchors:
        # Absolute corner-strength floor: on featureless black mat the relative qualityLevel of
        # goodFeaturesToTrack would otherwise accept JPEG noise as "features".
        strength = cv2.cornerMinEigenVal(self.ref, 5, 3)
        pts, owner = [], []
        self.real_features: list[int] = []
        for i, (cx, cy) in enumerate(self.ref_corners):
            mask = np.zeros_like(self.ref)
            cv2.circle(mask, (int(cx), int(cy)), self.radius, 255, -1)
            found = cv2.goodFeaturesToTrack(self.ref, maxCorners=self.cfg.points_per_corner, qualityLevel=0.01,
                                            minDistance=3, mask=mask, blockSize=5)
            found = [] if found is None else [p for p in found.reshape(-1, 2).tolist()
                                              if strength[int(p[1]), int(p[0])] >= self.cfg.min_feature_strength]
            self.real_features.append(len(found))
            found.append([float(cx), float(cy)])  # the clicked centre itself (tracked, never counted)
            pts.extend(found)
            owner.extend([i] * len(found))
        return _Anchors(np.array(pts, np.float32), np.array(owner))

    def _patch(self, image: np.ndarray, center: np.ndarray) -> np.ndarray | None:
        r = self.radius
        x, y = int(round(center[0])), int(round(center[1]))
        if x - r < 0 or y - r < 0 or x + r > image.shape[1] or y + r > image.shape[0]:
            return None
        return image[y - r:y + r, x - r:x + r]

    def corner_visibility(self, gray: np.ndarray, H: np.ndarray) -> list[float]:
        """Normalized cross-correlation of each landmark patch with its calibration appearance.
        A hand or object over a sticker drops this sharply, even if some feature points survive."""
        back = cv2.warpPerspective(gray, np.linalg.inv(H), (self.ref.shape[1], self.ref.shape[0]))
        scores = []
        for ref_patch, center in zip(self.ref_patches, self.ref_corners):
            cur = self._patch(back, center)
            if ref_patch is None or cur is None:
                scores.append(0.0)
                continue
            scores.append(float(cv2.matchTemplate(cur, ref_patch, cv2.TM_CCOEFF_NORMED)[0, 0]))
        return scores

    def feature_counts(self) -> list[int]:
        """Real texture features found around each landmark at calibration."""
        return list(self.real_features)

    def _reset(self) -> None:
        self.prev_gray: np.ndarray | None = None
        self.cur_points: np.ndarray | None = None  # aligned with anchors.ref_points
        self.prev_corners: np.ndarray | None = None  # accepted corners, working pixels
        self.smoothed: np.ndarray | None = None
        self.last_ok_at: float | None = None
        self.steady_frames = 0
        self.missing_since: float | None = None

    def lose(self) -> None:
        """Forget every transform. Nothing from before the loss can be reused."""
        if self.prev_corners is not None or self.cur_points is not None:
            self.epoch += 1
        self._reset()

    # -- per frame --------------------------------------------------------------------------------------

    def update(self, frame: np.ndarray, now: float) -> TrackResult:
        h, w = frame.shape[:2]
        if abs(w / h - self.cal.aspect) > 0.02:
            self.lose()
            return self._fail("Camera view changed. Recalibrate mat.", state="lost")
        gray, frame_scale = to_work(frame, self.cfg.work_long_side)
        if gray.shape != self.ref.shape:  # same aspect, different size: bring to reference size
            gray = cv2.resize(gray, (self.ref.shape[1], self.ref.shape[0]), interpolation=cv2.INTER_AREA)
        if self.last_ok_at is not None and now - self.last_ok_at > self.cfg.stale_s:
            self.lose()  # a transform older than stale_s is never reused

        predicted = self._predict(gray)
        if predicted is None:
            predicted = self._reacquire(gray)
            if predicted is None:
                self.lose()
                return self._fail(self._lost_message(gray))
        H, pts = self._refine(gray, predicted)
        if H is None:
            self.lose()
            return self._fail(self._lost_message(gray))
        result = self._validate(gray, H, pts, now)
        if result.state == "lost":
            self.lose()
            result.epoch = self.epoch
            if self._blurry(gray):  # the real cause is movement, not a hidden corner
                result.message = "Mat tracking lost. Hold the phone still."
            return result
        self.prev_gray = gray
        self.last_ok_at = now
        result.frame_size = (w, h)
        return self._finish(result, frame_scale_to_frame=w / self.ref.shape[1])

    def _predict(self, gray: np.ndarray) -> np.ndarray | None:
        """Frame-to-frame LK: a cheap prediction of the reference->current homography."""
        if self.prev_gray is None or self.cur_points is None:
            return None
        p0 = self.cur_points.reshape(-1, 1, 2)
        p1, st, _ = cv2.calcOpticalFlowPyrLK(self.prev_gray, gray, p0, None, winSize=(21, 21), maxLevel=3)
        if p1 is None:
            return None
        ok = st.reshape(-1) == 1
        if ok.sum() < self.cfg.min_inliers:
            return None
        H, _ = cv2.findHomography(self.anchors.ref_points[ok], p1.reshape(-1, 2)[ok], cv2.RANSAC, 4.0)
        return H

    def _reacquire(self, gray: np.ndarray) -> np.ndarray | None:
        """Find the mat from scratch by matching ORB features of the reference landmark patches."""
        if self.ref_desc is None or len(self.ref_kp) < 8:
            return None
        kp, desc = self.orb.detectAndCompute(gray, None)
        if desc is None or len(kp) < 8:
            return None
        matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(self.ref_desc, desc, k=2)
        good = [m for m, *rest in matches if rest and m.distance < 0.8 * rest[0].distance]
        if len(good) < self.cfg.min_inliers:
            return None
        src = np.float32([self.ref_kp[m.queryIdx].pt for m in good])
        dst = np.float32([kp[m.trainIdx].pt for m in good])
        H, inliers = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if H is None or inliers is None or int(inliers.sum()) < self.cfg.min_inliers:
            return None
        return H

    def _refine(self, gray: np.ndarray, H_pred: np.ndarray) -> tuple[np.ndarray | None, np.ndarray | None]:
        """Re-anchor to the reference: warp it into the current view, refine every point with LK,
        then fit reference -> current with RANSAC. Anchoring to the reference prevents drift."""
        h, w = gray.shape[:2]
        warped = cv2.warpPerspective(self.ref, H_pred, (w, h), flags=cv2.INTER_LINEAR)
        guess = cv2.perspectiveTransform(self.anchors.ref_points.reshape(-1, 1, 2), H_pred)
        p1, st, err = cv2.calcOpticalFlowPyrLK(warped, gray, guess, guess.copy(), winSize=(15, 15), maxLevel=2,
                                              flags=cv2.OPTFLOW_USE_INITIAL_FLOW)
        if p1 is None:
            return None, None
        back, st2, _ = cv2.calcOpticalFlowPyrLK(gray, warped, p1, guess.copy(), winSize=(15, 15), maxLevel=2,
                                                flags=cv2.OPTFLOW_USE_INITIAL_FLOW)
        fb = np.linalg.norm(back.reshape(-1, 2) - guess.reshape(-1, 2), axis=1)
        inside = (p1.reshape(-1, 2)[:, 0] >= 0) & (p1.reshape(-1, 2)[:, 0] < w) & \
                 (p1.reshape(-1, 2)[:, 1] >= 0) & (p1.reshape(-1, 2)[:, 1] < h)
        ok = (st.reshape(-1) == 1) & (st2.reshape(-1) == 1) & (fb < self.cfg.max_fb_error) & inside
        if ok.sum() < self.cfg.min_inliers:
            return None, None
        pts = p1.reshape(-1, 2)
        H, mask = cv2.findHomography(self.anchors.ref_points[ok], pts[ok], cv2.RANSAC, 3.0)
        if H is None:
            return None, None
        valid = np.zeros(len(pts), bool)
        valid[np.flatnonzero(ok)[mask.reshape(-1) == 1]] = True
        self.cur_points = pts.astype(np.float32)
        self._valid = valid
        return H, pts

    def _validate(self, gray: np.ndarray, H: np.ndarray, pts: np.ndarray, now: float) -> TrackResult:
        cfg = self.cfg
        valid = self._valid
        inliers = int(valid.sum())
        projected = cv2.perspectiveTransform(self.anchors.ref_points[valid].reshape(-1, 1, 2), H).reshape(-1, 2)
        reproj = float(np.sqrt(np.mean(np.sum((projected - pts[valid]) ** 2, axis=1)))) if inliers else float("inf")
        per_corner = [int(np.sum(valid & (self.anchors.corner_of == i))) for i in range(4)]
        visibility = self.corner_visibility(gray, H)
        seen = sum(c >= cfg.min_corner_inliers and v >= cfg.min_corner_ncc for c, v in zip(per_corner, visibility))
        corners = cv2.perspectiveTransform(self.ref_corners.reshape(-1, 1, 2), H).reshape(-1, 2)
        gh, gw = gray.shape[:2]
        base = dict(inliers=inliers, reproj_error=round(reproj, 3), corners_seen=seen)

        if inliers < cfg.min_inliers:
            return TrackResult("lost", "Mat tracking lost. Show all four corners.", **base)
        if reproj > cfg.max_reproj_px:
            return TrackResult("lost", "Mat tracking is unreliable. Hold the phone still.", **base)
        margin = -cfg.corner_margin_frac * max(gw, gh)
        if np.any(corners < margin) or np.any(corners[:, 0] > gw - margin) or np.any(corners[:, 1] > gh - margin):
            return TrackResult("lost", "Show all four corners.", **base)
        problem = quad_problem(np.clip(corners, 0, [gw, gh]), gw, gh, cfg)
        if problem:
            return TrackResult("lost", "Mat tracking lost. Show all four corners.", **base)
        if seen < 4:
            self.missing_since = self.missing_since or now
            if seen < 3 or now - self.missing_since > cfg.max_missing_corner_s:
                return TrackResult("lost", "Show all four corners.", **base)
        else:
            self.missing_since = None
        motion = None
        if self.prev_corners is not None:
            diag = math.hypot(gw, gh)
            jump = float(np.max(np.linalg.norm(corners - self.prev_corners, axis=1)))
            area_now = cv2.contourArea(corners.astype(np.float32))
            area_prev = cv2.contourArea(self.prev_corners.astype(np.float32))
            if jump > cfg.max_jump_frac * diag or abs(area_now / area_prev - 1) > cfg.max_area_change:
                return TrackResult("lost", "The camera moved suddenly. Hold the phone still.", **base)
            motion = float(np.mean(np.linalg.norm(corners - self.prev_corners, axis=1)))
        blur = sharpness(gray, corners, self.radius)
        steady = motion is not None and motion <= cfg.steady_px and blur >= cfg.blur_ratio * self.ref_sharpness
        self.steady_frames = self.steady_frames + 1 if steady else 0
        self.prev_corners = corners
        trustworthy = seen == 4 and self.steady_frames >= cfg.steady_frames
        if trustworthy:
            return TrackResult("tracking", "Mat tracking", True, corners=corners, motion=motion, **base)
        if seen < 4:
            message = "A corner sticker is covered. Show all four corners."
        elif blur < cfg.blur_ratio * self.ref_sharpness or (motion is not None and motion > cfg.steady_px):
            message = "Hold the phone still."
        else:
            message = "Settling… hold still."
        return TrackResult("unsteady", message, False, corners=corners, motion=motion, **base)

    def _finish(self, result: TrackResult, frame_scale_to_frame: float) -> TrackResult:
        corners = result.corners
        # Light smoothing of sub-pixel jitter; any real movement snaps immediately (no stale lag).
        if self.smoothed is not None and np.max(np.linalg.norm(corners - self.smoothed, axis=1)) < self.cfg.smooth_px:
            corners = self.smoothed + self.cfg.smooth_alpha * (corners - self.smoothed)
        self.smoothed = corners
        frame_corners = corners * frame_scale_to_frame
        result.corners = frame_corners
        result.homography = homography_to_canonical(frame_corners, self.cal.canonical_size)
        result.epoch = self.epoch
        return result

    def _blurry(self, gray: np.ndarray) -> bool:
        return float(cv2.Laplacian(gray, cv2.CV_64F).var()) < self.cfg.blur_ratio * self.ref_global_sharpness

    def _lost_message(self, gray: np.ndarray) -> str:
        return "Mat tracking lost. Hold the phone still." if self._blurry(gray) else "Mat tracking lost. Show all four corners."

    def _fail(self, message: str, state: TrackState = "lost") -> TrackResult:
        return TrackResult(state, message, epoch=self.epoch)


def warp_canonical(frame: np.ndarray, homography: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    return cv2.warpPerspective(frame, homography, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)


# -- per-source service -----------------------------------------------------------------------------------

MatState = Literal["off", "uncalibrated", "tracking", "unsteady", "lost", "recalibrate"]


@dataclass
class MatFrame:
    """What the session should do with one camera frame."""

    state: MatState
    message: str
    bypass: bool  # no mat workflow for this source: evaluate the raw frame as before
    canonical: np.ndarray | None = None  # top-down mat image (unmasked, for display)
    detect: np.ndarray | None = None  # same image with the landmark band blacked out (for detectors)
    trustworthy: bool = False
    track: TrackResult | None = None


@dataclass
class _SourceMat:
    calibration: MatCalibration | None = None
    tracker: MatTracker | None = None
    problem: str = ""  # malformed/missing calibration file: never a pass, ask to recalibrate
    last: MatFrame | None = None


class MatService:
    """Calibrations per camera source (laptop webcam, phone), stored apart from procedures and setups."""

    def __init__(self, cfg: MatConfig, directory: Path | None) -> None:
        self.cfg = cfg
        self.directory = directory
        self.sources: dict[str, _SourceMat] = {"webcam": _SourceMat(), "phone": _SourceMat()}
        if directory is not None:
            for source in self.sources:
                self._load(source)

    # -- persistence ------------------------------------------------------------------------------------

    def _paths(self, source: str) -> tuple[Path, Path]:
        return self.directory / f"{source}.json", self.directory / f"{source}.png"

    def _load(self, source: str) -> None:
        meta, image = self._paths(source)
        if not meta.exists():
            return
        try:
            cal = MatCalibration.model_validate(json.loads(meta.read_text(encoding="utf-8")))
            if cal.source != source:
                raise ValueError("calibration belongs to another camera")
            ref = cv2.imread(str(image), cv2.IMREAD_GRAYSCALE)
            if ref is None or abs(ref.shape[1] / ref.shape[0] - cal.aspect) > 0.02:
                raise ValueError("reference image missing or does not match")
            self.sources[source] = _SourceMat(cal, MatTracker(cal, ref, self.cfg))
        except Exception as exc:
            self.sources[source] = _SourceMat(problem=f"Saved mat calibration is unreadable ({type(exc).__name__}). "
                                                      "Recalibrate mat.")

    def _save(self, source: str, cal: MatCalibration, reference: np.ndarray) -> None:
        if self.directory is None:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        meta, image = self._paths(source)
        cv2.imwrite(str(image), reference)
        tmp = meta.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cal.to_json(), indent=2), encoding="utf-8")
        tmp.replace(meta)

    # -- commands ---------------------------------------------------------------------------------------

    def calibrate(self, source: str, frame: np.ndarray, points_norm, now: float | None = None) -> MatCalibration:
        """Validate and store a calibration; raises ValueError with a user-facing reason."""
        if source not in self.sources:
            raise ValueError("The simulator does not need mat calibration.")
        cal = build_calibration(source, frame, points_norm, self.cfg, now)  # type: ignore[arg-type]
        reference, _ = to_work(frame, self.cfg.work_long_side)
        tracker = MatTracker(cal, reference, self.cfg)
        counts = tracker.feature_counts()
        if min(counts) < self.cfg.min_corner_features:
            weak = LANDMARKS[int(np.argmin(counts))]
            raise ValueError(f"Not enough detail around the {weak}. Click the centre of the sticker, "
                             "improve lighting, or move the camera closer.")
        self._save(source, cal, reference)
        self.sources[source] = _SourceMat(cal, tracker)
        return cal

    def clear(self, source: str) -> None:
        if source not in self.sources:
            return
        self.sources[source] = _SourceMat()
        if self.directory is not None:
            for path in self._paths(source):
                path.unlink(missing_ok=True)

    def calibration(self, source: str) -> MatCalibration | None:
        entry = self.sources.get(source)
        return entry.calibration if entry else None

    def epoch(self, source: str) -> int:
        entry = self.sources.get(source)
        return entry.tracker.epoch if entry and entry.tracker else 0

    def lose(self, source: str) -> None:
        entry = self.sources.get(source)
        if entry and entry.tracker:
            entry.tracker.lose()

    # -- frames -----------------------------------------------------------------------------------------

    def process(self, source: str, frame: np.ndarray, now: float) -> MatFrame:
        entry = self.sources.get(source)
        if entry is None:  # simulator: already a top-down picture with the classic zones
            return MatFrame("off", "", bypass=True)
        if entry.problem:
            result = MatFrame("recalibrate", entry.problem, bypass=False)
        elif entry.tracker is None:
            result = MatFrame("uncalibrated", "Mat not calibrated.", bypass=True)
        else:
            track = entry.tracker.update(frame, now)
            if track.homography is None:
                state: MatState = "recalibrate" if "Recalibrate" in track.message else "lost"
                result = MatFrame(state, track.message, bypass=False, track=track)
            else:
                size = entry.calibration.canonical_size
                canonical = warp_canonical(frame, track.homography, size)
                result = MatFrame(track.state, track.message, bypass=False, canonical=canonical,
                                  detect=mask_band(canonical, self.cfg.band_fraction),
                                  trustworthy=track.trustworthy, track=track)
        entry.last = result
        return result

    def status(self, source: str) -> dict:
        entry = self.sources.get(source)
        if entry is None:
            return {"source": source, "state": "off", "message": "", "calibrated": False, "trustworthy": False,
                    "corners": None, "canonicalAspect": None, "band": self.cfg.band_fraction, "metrics": None}
        last = entry.last
        cal = entry.calibration
        track = last.track if last else None
        corners = None
        if track is not None and track.corners is not None and track.frame_size and last.canonical is not None:
            corners = [[round(float(x), 4), round(float(y), 4)]
                       for x, y in track.corners / np.array(track.frame_size, dtype=np.float64)]
        state = last.state if last else ("recalibrate" if entry.problem else "uncalibrated" if not cal else "lost")
        message = last.message if last else (entry.problem or ("Mat not calibrated." if not cal else "Waiting for the camera…"))
        return {
            "source": source,
            "state": state,
            "message": message,
            "calibrated": cal is not None,
            "trustworthy": bool(last and last.trustworthy),
            "corners": corners,
            "canonicalAspect": round(cal.canonical_width / cal.canonical_height, 4) if cal else None,
            "band": self.cfg.band_fraction,
            "metrics": None if track is None else {
                "inliers": track.inliers,
                "reprojError": track.reproj_error,
                "motion": None if track.motion is None else round(track.motion, 2),
            },
        }
