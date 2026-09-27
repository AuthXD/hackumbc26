"""Offline mat calibration check: validate TL/TR/BR/BL clicks and warp the canonical mat.

Does not start the live server, touch the database, or use API keys.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

from app.config import REPO_ROOT, MatConfig, settings
from app.mat import (
    LANDMARKS,
    canonical_size,
    homography_to_canonical,
    mask_band,
    quad_problem,
    warp_canonical,
)

DEFAULT_IMAGE = Path(
    os.environ.get("TEACHBACK_MAT_CHECK_IMAGE")
    or r"C:\Users\Komal Tummala\Downloads\IMG_4738.jpg"
)
DEFAULT_FIXTURE = REPO_ROOT / "benchmarks" / "mat" / "img_4738.landmarks.json"
DEFAULT_OUTPUT = REPO_ROOT / "benchmarks" / "mat" / "results"

NAMED_ORDER = (
    ("TL", "purple creature"),
    ("TR", "frog"),
    ("BR", "potion bottle"),
    ("BL", "SteelSeries logo"),
)


class MatCheckError(Exception):
    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.message = message
        self.code = code


def load_fixture(path: Path) -> tuple[np.ndarray, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MatCheckError(f"Invalid fixture {path}: {type(exc).__name__}") from exc
    order = data.get("order") or []
    if len(order) != 4:
        raise MatCheckError("Fixture must list exactly four landmarks in TL, TR, BR, BL order.")
    expected = [pair[0] for pair in NAMED_ORDER]
    got = [str(item.get("id", "")) for item in order]
    if got != expected:
        raise MatCheckError(f"Fixture order must be {expected}; got {got}.")
    try:
        points = np.array([[float(item["x"]), float(item["y"])] for item in order], dtype=np.float64)
        width, height = int(data["image_width"]), int(data["image_height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MatCheckError(f"Fixture is missing coordinates or dimensions ({type(exc).__name__}).") from exc
    if width <= 0 or height <= 0:
        raise MatCheckError("Fixture image_width and image_height must be positive.")
    return points, {**data, "points_px": points.tolist(), "image_width": width, "image_height": height}


def annotate_source(image: np.ndarray, points: np.ndarray) -> np.ndarray:
    out = image.copy()
    h, w = out.shape[:2]
    pts = [(int(round(x)), int(round(y))) for x, y in points]
    cv2.polylines(out, [np.array(pts, np.int32)], True, (0, 210, 255), 6, cv2.LINE_AA)
    for i, ((x, y), (_sid, name)) in enumerate(zip(pts, NAMED_ORDER), start=1):
        cv2.circle(out, (x, y), 18, (0, 210, 255), -1, cv2.LINE_AA)
        label = f"{i} {name}"
        tx, ty = min(w - 20, x + 24), max(36, y - 16)
        cv2.putText(out, label, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 3, cv2.LINE_AA)
    return out


def annotate_canonical(canonical: np.ndarray, band: float) -> np.ndarray:
    vis = canonical.copy()
    overlay = mask_band(canonical, band)
    vis = cv2.addWeighted(vis, 0.45, overlay, 0.55, 0)
    h, w = vis.shape[:2]
    bx, by = round(w * band), round(h * band)
    cv2.rectangle(vis, (bx, by), (w - bx, h - by), (0, 200, 255), 3, cv2.LINE_AA)
    cv2.putText(vis, "excluded 10% sticker band", (bx + 8, by + 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2, cv2.LINE_AA)
    return vis


def run(image_path: Path, fixture_path: Path, output_dir: Path) -> dict:
    if not image_path.is_file():
        raise MatCheckError(f"Missing source image: {image_path}")
    if not fixture_path.is_file():
        raise MatCheckError(f"Missing fixture: {fixture_path}")
    points, meta = load_fixture(fixture_path)
    image = cv2.imread(str(image_path))
    if image is None:
        raise MatCheckError(f"Could not decode source image: {image_path}")
    height, width = image.shape[:2]
    if (width, height) != (meta["image_width"], meta["image_height"]):
        raise MatCheckError(
            f"Image is {width}x{height} but fixture is {meta['image_width']}x{meta['image_height']}."
        )
    cfg = MatConfig()
    problem = quad_problem(points, width, height, cfg)
    if problem:
        raise MatCheckError(f"Quadrilateral validation failed: {problem}")
    size = canonical_size(points, settings.mat.canonical_long_side)
    try:
        H = homography_to_canonical(points, size)
        canonical = warp_canonical(image, H, size)
    except cv2.error as exc:
        raise MatCheckError(f"Warp failed ({type(exc).__name__}).") from exc
    if canonical is None or canonical.size == 0:
        raise MatCheckError("Warp failed: empty canonical image.")
    output_dir.mkdir(parents=True, exist_ok=True)
    source_out = output_dir / "annotated-source.png"
    canonical_out = output_dir / "canonical.png"
    band_out = output_dir / "canonical-band.png"
    report_out = output_dir / "mat-check.json"
    if not cv2.imwrite(str(source_out), annotate_source(image, points)):
        raise MatCheckError(f"Could not write {source_out}")
    if not cv2.imwrite(str(canonical_out), canonical):
        raise MatCheckError(f"Could not write {canonical_out}")
    if not cv2.imwrite(str(band_out), annotate_canonical(canonical, settings.mat.band_fraction)):
        raise MatCheckError(f"Could not write {band_out}")
    report = {
        "ok": True,
        "source": str(image_path),
        "fixture": str(fixture_path),
        "source_width": width,
        "source_height": height,
        "canonical_width": size[0],
        "canonical_height": size[1],
        "band_fraction": settings.mat.band_fraction,
        "points_px": points.round(2).tolist(),
        "points_norm": (points / [width, height]).round(6).tolist(),
        "order": [
            {"id": sid, "name": name, "landmark": LANDMARKS[i]}
            for i, (sid, name) in enumerate(NAMED_ORDER)
        ],
        "quad_valid": True,
        "outputs": {"annotated_source": str(source_out), "canonical": str(canonical_out), "band": str(band_out)},
    }
    report_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        run(args.image, args.fixture, args.output)
    except MatCheckError as exc:
        print(exc.message, file=sys.stderr)
        return exc.code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
