"""A synthetic mat seen by a moving camera, for offline stabilization tests.

The "world" is a flat top-down picture: textured table, dark mat, four textured corner stickers (stand-ins
for the creature/frog/potion/logo landmarks) and optional colored blocks at known mat coordinates.
Camera frames are the world warped by a homography, JPEG round-tripped like real phone frames.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

WORLD = (900, 1500)  # world picture (w, h): a portrait mat, like the real one
FRAME = (640, 1138)  # what the phone actually sends (portrait)
# Landmark centres in the world, TL, TR, BR, BL (inset from the mat corners).
LANDMARKS = np.array([[190, 190], [710, 205], [720, 1300], [180, 1310]], np.float64)
BGR = {"red": (40, 40, 215), "yellow": (40, 215, 240), "green": (70, 170, 50), "blue": (200, 100, 30)}


def _sticker(rng: np.random.Generator, size: int = 110) -> tuple[np.ndarray, np.ndarray]:
    patch = np.zeros((size, size, 3), np.uint8)
    cv2.circle(patch, (size // 2, size // 2), size // 2 - 2, (235, 235, 235), -1)  # white sticker border
    for _ in range(14):
        color = tuple(int(c) for c in rng.integers(20, 240, 3))
        kind = rng.integers(0, 3)
        c = tuple(int(v) for v in rng.integers(18, size - 18, 2))
        if kind == 0:
            cv2.circle(patch, c, int(rng.integers(4, 16)), color, -1)
        elif kind == 1:
            d = tuple(int(v) for v in rng.integers(18, size - 18, 2))
            cv2.line(patch, c, d, color, int(rng.integers(2, 6)))
        else:
            cv2.rectangle(patch, c, (c[0] + int(rng.integers(6, 20)), c[1] + int(rng.integers(6, 20))), color, -1)
    mask = np.zeros((size, size), np.uint8)
    cv2.circle(mask, (size // 2, size // 2), size // 2 - 2, 255, -1)
    return patch, mask


def mat_point(u: float, v: float) -> tuple[int, int]:
    """World pixel of a point given in landmark-rectangle coordinates (u, v in 0..1)."""
    src = np.float32([[0, 0], [1, 0], [1, 1], [0, 1]])
    H = cv2.getPerspectiveTransform(src, LANDMARKS.astype(np.float32))
    x, y = cv2.perspectiveTransform(np.float32([[[u, v]]]), H)[0, 0]
    return int(round(x)), int(round(y))


def world(blocks: dict[str, tuple[float, float]] | None = None, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    w, h = WORLD
    table = rng.normal(165, 22, (h // 8, w // 8, 3)).clip(0, 255).astype(np.uint8)
    img = cv2.resize(table, (w, h), interpolation=cv2.INTER_CUBIC)
    cv2.rectangle(img, (110, 110), (w - 110, h - 110), (22, 22, 24), -1)  # the black mat
    for cx, cy in LANDMARKS:
        patch, mask = _sticker(rng)
        s = patch.shape[0]
        x0, y0 = int(cx) - s // 2, int(cy) - s // 2
        roi = img[y0:y0 + s, x0:x0 + s]
        roi[mask > 0] = patch[mask > 0]
    for color, (u, v) in (blocks or {}).items():
        x, y = mat_point(u, v)
        cv2.rectangle(img, (x - 34, y - 34), (x + 34, y + 34), BGR[color], -1)
    return img


def base_homography() -> np.ndarray:
    """World -> frame for a camera looking straight down with the mat filling most of the picture."""
    s = FRAME[1] / WORLD[1] * 0.96
    return np.array([[s, 0, (FRAME[0] - WORLD[0] * s) / 2], [0, s, FRAME[1] * 0.02], [0, 0, 1]], np.float64)


def motion(tx: float = 0, ty: float = 0, rot: float = 0, scale: float = 1, px: float = 0, py: float = 0) -> np.ndarray:
    """A camera move around the frame centre: translation, rotation (deg), zoom and perspective tilt."""
    c = np.array([[1, 0, -FRAME[0] / 2], [0, 1, -FRAME[1] / 2], [0, 0, 1]], np.float64)
    a = math.radians(rot)
    rs = np.array([[scale * math.cos(a), -scale * math.sin(a), 0], [scale * math.sin(a), scale * math.cos(a), 0],
                   [0, 0, 1]])
    persp = np.array([[1, 0, 0], [0, 1, 0], [px, py, 1]], np.float64)
    shift = np.array([[1, 0, tx], [0, 1, ty], [0, 0, 1]], np.float64)
    return shift @ np.linalg.inv(c) @ persp @ rs @ c


def frame(world_img: np.ndarray, H: np.ndarray, quality: int = 80) -> np.ndarray:
    img = cv2.warpPerspective(world_img, H, FRAME, flags=cv2.INTER_AREA, borderValue=(150, 155, 160))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def jpeg(img: np.ndarray, quality: int = 80) -> bytes:
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()


def landmarks_in_frame(H: np.ndarray) -> np.ndarray:
    return cv2.perspectiveTransform(LANDMARKS.reshape(-1, 1, 2), H).reshape(-1, 2)


def normalized_landmarks(H: np.ndarray) -> list[list[float]]:
    return (landmarks_in_frame(H) / np.array(FRAME, np.float64)).tolist()


def cover(img: np.ndarray, H: np.ndarray, corner: int, radius: int = 48) -> np.ndarray:
    """A hand over one landmark sticker."""
    out = img.copy()
    x, y = landmarks_in_frame(H)[corner]
    cv2.circle(out, (int(x), int(y)), radius, (120, 150, 190), -1)
    return out
