"""Draws fake tabletop frames: colored blocks on a neutral surface, placed by zone."""

from __future__ import annotations

import cv2
import numpy as np

from app.config import default_zones

W, H = 640, 480
BGR = {
    "red": (40, 40, 215),
    "yellow": (40, 215, 240),
    "green": (70, 170, 50),
    "blue": (200, 100, 30),
}
SKIN = (140, 170, 220)


def blank() -> np.ndarray:
    img = np.full((H, W, 3), (150, 160, 165), np.uint8)  # grey-beige table
    return img


def zone_center(zone_id: str, slot: int = 0) -> tuple[int, int]:
    """Pixel center of a zone; `slot` spreads several objects vertically within the zone."""
    z = next(z for z in default_zones() if z.id == zone_id)
    cx = (z.x + z.w / 2) * W
    cy = (z.y + z.h * (0.2 + 0.2 * slot)) * H
    return int(cx), int(cy)


def draw_block(img, color: str, center: tuple[int, int], half: int = 28, bgr=None) -> None:
    cx, cy = center
    cv2.rectangle(img, (cx - half, cy - half), (cx + half, cy + half), bgr or BGR[color], -1)


def as_jpeg_roundtrip(img: np.ndarray) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
    assert ok
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)
