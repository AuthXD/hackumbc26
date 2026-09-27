"""Mat calibration geometry, tracking under camera motion, fail-closed behaviour and persistence."""

import json

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from app.config import REPO_ROOT, MatConfig, VisionConfig
from app.mat import (
    MatCalibration,
    MatService,
    build_calibration,
    canonical_size,
    canonical_vision,
    canonical_zones,
    homography_to_canonical,
    prepare_saved_calibration,
    in_workspace,
    mask_band,
    quad_problem,
)

from .synthetic_mat import (
    FRAME,
    base_homography,
    cover,
    frame,
    landmarks_in_frame,
    mat_point,
    motion,
    normalized_landmarks,
    world,
)

CFG = MatConfig()
W, H = FRAME
GOOD = np.array([[100, 80], [540, 90], [560, 1050], [90, 1040]], np.float64)


# -- calibration geometry --------------------------------------------------------------------------


def test_valid_quad_and_normalized_calibration():
    assert quad_problem(GOOD, W, H, CFG) is None
    img = np.zeros((H, W, 3), np.uint8)
    cal = build_calibration("phone", img, GOOD / [W, H], CFG, now=1.0)
    assert cal.points[0] == pytest.approx((100 / W, 80 / H))
    assert all(0 <= c <= 1 for p in cal.points for c in p)
    assert (cal.frame_width, cal.frame_height) == (W, H)
    cw, ch = cal.canonical_size
    assert cw > ch and cw == CFG.canonical_long_side and ch / cw == pytest.approx(455 / 960, abs=0.02)
    assert cal.orientation == "landscape-ccw"


@pytest.mark.parametrize("points,reason", [
    (GOOD[[0, 3, 2, 1]], "Wrong order"),  # counter-clockwise: TL, BL, BR, TR
    (GOOD[[0, 2, 1, 3]], "cross"),  # crossed "bow tie"
    (np.array([[100, 80], [320, 68], [540, 90], [560, 1050]], float), "skewed"),  # a corner nearly on a line
    (np.array([[100, 80], [104, 82], [560, 1050], [90, 1040]], float), "too close"),  # collapsed corner
    (np.array([[300, 500], [372, 500], [372, 572], [300, 572]], float), "too small"),
    (np.array([[100, 80], [540, 90], [700, 1050], [90, 1040]], float), "inside the camera view"),
    (np.array([[100, 80], [540, 90], [330, 200], [90, 1040]], float), "cross"),  # concave dart
    (np.array([[250, 80], [390, 80], [620, 1100], [20, 1100]], float), "distorted"),  # wild keystone
])
def test_invalid_selections_are_rejected(points, reason):
    problem = quad_problem(points, W, H, CFG)
    assert problem is not None and reason.lower() in problem.lower(), problem
    with pytest.raises(ValueError):
        build_calibration("phone", np.zeros((H, W, 3), np.uint8), points / [W, H], CFG)


def test_calibration_model_rejects_malformed_data():
    good = build_calibration("phone", np.zeros((H, W, 3), np.uint8), GOOD / [W, H], CFG, now=1.0).to_json()
    for bad in ({**good, "points": [[1.5, 0], [1, 0], [1, 1], [0, 1]]},
                {**good, "points": [list(p) for p in (GOOD[[0, 2, 1, 3]] / [W, H])]},
                {**good, "points": good["points"][:3]},
                {**good, "frameWidth": 0},
                {**good, "source": "sim"}):
        with pytest.raises(ValidationError):
            MatCalibration.model_validate(bad)


def test_portrait_quad_rotates_ccw_into_a_landscape_canonical():
    across, down = (np.linalg.norm(GOOD[1] - GOOD[0]) + np.linalg.norm(GOOD[2] - GOOD[3])) / 2, (
        np.linalg.norm(GOOD[3] - GOOD[0]) + np.linalg.norm(GOOD[2] - GOOD[1])) / 2
    size = canonical_size(GOOD, 960)
    assert size[0] > size[1]
    assert size[0] / size[1] == pytest.approx(down / across, rel=0.02)  # the short edge is not stretched
    Hm = homography_to_canonical(GOOD, size)
    mapped = cv2.perspectiveTransform(GOOD.reshape(-1, 1, 2), Hm).reshape(-1, 2)
    w, h = size
    # raw TL, TR, BR, BL → canonical BL, TL, TR, BR
    assert mapped == pytest.approx(np.array([[0, h], [0, 0], [w, 0], [w, h]]), abs=1e-3)
    zones = canonical_zones(size, 0.1)

    def zone_at(point):
        hit = cv2.perspectiveTransform(np.float32([[point]]), Hm)[0, 0]
        nx, ny = float(hit[0] / w), float(hit[1] / h)
        return next(z.id for z in zones if z.contains(nx, ny))

    center = GOOD.mean(axis=0)
    top = (GOOD[0] + GOOD[1]) / 2
    bottom = (GOOD[3] + GOOD[2]) / 2
    assert zone_at(top * 0.72 + center * 0.28) == "A"  # physical top third → left
    assert zone_at(center) == "B"
    assert zone_at(bottom * 0.72 + center * 0.28) == "C"  # physical bottom third → right


def test_raw_preview_is_not_an_overlay_and_stacks_on_a_narrow_window():
    css = (REPO_ROOT / "frontend/src/styles.css").read_text(encoding="utf-8")
    assert "raw-layer.inset" not in css
    rule = css.split("@media (max-width: 800px)", 1)[1]
    assert "grid-template-columns: 1fr" in rule.split("}", 1)[0] or "grid-template-columns: 1fr" in rule[:400]


def test_canonical_zones_run_left_to_right():
    zones = canonical_zones((960, 400), 0.1)
    assert [z.id for z in zones] == ["A", "B", "C"]
    assert all(z.y == pytest.approx(0.1) and z.h == pytest.approx(0.8) for z in zones)
    assert zones[0].x == pytest.approx(0.1)
    assert zones[0].x + zones[0].w == pytest.approx(zones[1].x)
    assert zones[1].x + zones[1].w == pytest.approx(zones[2].x)
    assert zones[2].x + zones[2].w == pytest.approx(0.9)
    assert sum(z.w for z in zones) == pytest.approx(0.8)
    assert in_workspace(0.5, 0.5, 0.1) and not in_workspace(0.05, 0.5, 0.1) and not in_workspace(0.5, 0.95, 0.1)
    vision = canonical_vision(VisionConfig(), (960, 400), 0.1)
    assert [z.id for z in vision.zones] == ["A", "B", "C"] and VisionConfig().zones[0].y == 0.12
    masked = mask_band(np.full((400, 960, 3), 200, np.uint8), 0.1)
    assert masked[:, :96].max() == 0 and masked[:40].max() == 0 and masked[200, 480].min() == 200


# -- tracking --------------------------------------------------------------------------------------

WORLD_BLOCK = world({"red": (0.5, 0.5)})
H0 = base_homography()


def calibrated() -> MatService:
    svc = MatService(CFG, None)
    svc.calibrate("phone", frame(WORLD_BLOCK, H0), normalized_landmarks(H0))
    return svc


def run(svc, H, frames=5, t0=0.0, world_img=WORLD_BLOCK):
    out = None
    for i in range(frames):
        out = svc.process("phone", frame(world_img, H), t0 + i * 0.2)
    return out


def red_centre(canonical: np.ndarray) -> tuple[float, float]:
    mask = cv2.inRange(canonical, (0, 0, 150), (90, 90, 255))
    m = cv2.moments(mask)
    assert m["m00"] > 0, "red block not visible in the canonical view"
    return m["m10"] / m["m00"] / canonical.shape[1], m["m01"] / m["m00"] / canonical.shape[0]


@pytest.mark.parametrize("name,move", [
    ("still", motion()),
    ("translation", motion(tx=40, ty=-35)),
    ("rotation", motion(rot=7)),
    ("scale", motion(scale=0.9)),
    ("perspective", motion(px=0.00012, py=-0.00008)),
    ("combined", motion(tx=-25, ty=20, rot=-5, scale=0.93, px=-0.0001)),
])
def test_canonical_view_is_stable_under_camera_motion(name, move):
    svc = calibrated()
    reference = red_centre(run(svc, H0).canonical)
    result = None
    # Move gradually (a hand-held phone), then hold still.
    for k in range(1, 11):
        step = np.eye(3) + (move - np.eye(3)) * (k / 10)
        result = svc.process("phone", frame(WORLD_BLOCK, step @ H0), 1 + k * 0.2)
    result = run(svc, move @ H0, frames=5, t0=4)
    assert result.state == "tracking" and result.trustworthy, (name, result.message)
    centre = red_centre(result.canonical)
    assert centre == pytest.approx(reference, abs=0.006), name  # < 0.6% of the mat
    corners = result.track.corners
    assert np.max(np.linalg.norm(corners - landmarks_in_frame(move @ H0), axis=1)) < 1.5


def test_trust_requires_a_steady_camera():
    svc = calibrated()
    first = svc.process("phone", frame(WORLD_BLOCK, H0), 0.0)
    assert first.state != "tracking" and not first.trustworthy  # never trusted on the first frame
    assert run(svc, H0, frames=4, t0=0.2).trustworthy
    moving = [svc.process("phone", frame(WORLD_BLOCK, motion(tx=6 * k) @ H0), 1 + 0.2 * k) for k in range(1, 5)]
    assert not any(r.trustworthy for r in moving) and "still" in moving[-1].message.lower()


def test_occlusion_blur_jump_and_out_of_frame_fail_closed_then_recover():
    svc = calibrated()
    assert run(svc, H0).trustworthy
    t = 2.0
    covered = []
    for _ in range(8):  # a hand over the potion sticker for 1.6 s
        covered.append(svc.process("phone", cover(frame(WORLD_BLOCK, H0), H0, 2), t))
        t += 0.2
    assert not any(r.trustworthy for r in covered)
    assert covered[0].state == "unsteady" and "covered" in covered[0].message
    assert any(r.state == "lost" for r in covered)  # tolerated briefly, then treated as lost

    blurred = cv2.GaussianBlur(frame(WORLD_BLOCK, H0), (0, 0), 6)
    r = svc.process("phone", blurred, t)
    assert r.state == "lost" and "still" in r.message.lower() and r.canonical is None
    t += 0.2
    assert run(svc, H0, frames=6, t0=t).trustworthy  # recovers once sharp again
    t += 1.4

    epoch = svc.epoch("phone")
    jumped = motion(tx=45, ty=30, rot=6) @ H0
    r = svc.process("phone", frame(WORLD_BLOCK, jumped), t)
    assert r.state == "lost" and r.canonical is None and svc.epoch("phone") > epoch
    assert run(svc, jumped, frames=6, t0=t + 0.2).trustworthy  # re-acquired, then trusted after settling
    t += 1.6

    gone = motion(tx=330) @ H0
    r = svc.process("phone", frame(WORLD_BLOCK, gone), t)
    assert r.state == "lost" and "four corners" in r.message
    r = svc.process("phone", np.zeros((H, W, 3), np.uint8), t + 0.2)
    assert r.state == "lost" and r.canonical is None


def test_no_stale_transform_after_loss_or_gap():
    svc = calibrated()
    good = run(svc, H0)
    assert good.trustworthy and good.track.homography is not None
    lost = svc.process("phone", np.zeros((H, W, 3), np.uint8), 2.0)
    assert lost.track.homography is None and lost.canonical is None and lost.detect is None
    tracker = svc.sources["phone"].tracker
    assert tracker.prev_corners is None and tracker.cur_points is None  # nothing survives the loss
    run(svc, H0, frames=5, t0=3.0)
    tracker.update(frame(WORLD_BLOCK, H0), 3.8)
    gap = svc.process("phone", frame(WORLD_BLOCK, H0), 10.0)  # frames stopped for 6 s
    assert not gap.trustworthy  # the old transform was dropped; trust must be re-earned


def test_calibration_needs_texture_at_every_corner():
    svc = MatService(CFG, None)
    blank_corner = normalized_landmarks(H0)
    x, y = cv2.perspectiveTransform(np.array([[[600, 330]]], np.float64), H0)[0, 0]
    blank_corner[1] = [x / W, y / H]  # plain black mat just inside the frog, not the sticker
    with pytest.raises(ValueError, match="Not enough detail"):
        svc.calibrate("phone", frame(WORLD_BLOCK, H0), blank_corner)
    with pytest.raises(ValueError, match="simulator"):
        svc.calibrate("sim", frame(WORLD_BLOCK, H0), normalized_landmarks(H0))


def test_camera_view_change_asks_for_recalibration():
    svc = calibrated()
    landscape = cv2.resize(frame(WORLD_BLOCK, H0), (1138, 640))
    r = svc.process("phone", landscape, 1.0)
    assert r.state == "recalibrate" and "Recalibrate" in r.message and r.canonical is None


# -- persistence -----------------------------------------------------------------------------------


def test_saved_portrait_calibration_rotates_and_a_wide_one_is_rejected(tmp_path):
    folder = tmp_path / "mat"
    fresh = MatService(CFG, folder)
    fresh.calibrate("phone", frame(WORLD_BLOCK, H0), normalized_landmarks(H0))
    saved = json.loads((folder / "phone.json").read_text(encoding="utf-8"))
    assert saved["canonicalWidth"] > saved["canonicalHeight"]
    portrait = {**saved, "version": 1, "canonicalWidth": saved["canonicalHeight"],
                "canonicalHeight": saved["canonicalWidth"]}
    portrait.pop("orientation", None)
    (folder / "phone.json").write_text(json.dumps(portrait), encoding="utf-8")
    migrated = MatService(CFG, folder)
    cal = migrated.calibration("phone")
    assert cal is not None and cal.canonical_width > cal.canonical_height and cal.orientation == "landscape-ccw"
    warped = run(migrated, H0)
    assert warped.canonical is not None and warped.canonical.shape[1] > warped.canonical.shape[0]
    on_disk = json.loads((folder / "phone.json").read_text(encoding="utf-8"))
    assert on_disk["orientation"] == "landscape-ccw" and on_disk["canonicalWidth"] > on_disk["canonicalHeight"]

    wide = {**saved, "version": 1, "canonicalWidth": 960, "canonicalHeight": 400}
    wide.pop("orientation")
    (folder / "phone.json").write_text(json.dumps(wide), encoding="utf-8")
    rejected = MatService(CFG, folder)
    assert rejected.calibration("phone") is None
    problem = rejected.process("phone", frame(WORLD_BLOCK, H0), 0)
    assert problem.state == "recalibrate" and "Recalibrate mat" in problem.message and problem.canonical is None

    broken = {**saved, "orientation": "landscape-ccw", "canonicalWidth": 400, "canonicalHeight": 960}
    with pytest.raises(ValueError, match="Recalibrate mat"):
        prepare_saved_calibration(broken)


def test_calibration_persists_per_camera_and_malformed_files_fail_closed(tmp_path):
    folder = tmp_path / "mat"
    svc = MatService(CFG, folder)
    svc.calibrate("phone", frame(WORLD_BLOCK, H0), normalized_landmarks(H0))
    assert sorted(p.name for p in folder.iterdir()) == ["phone.json", "phone.png"]
    reloaded = MatService(CFG, folder)
    assert reloaded.calibration("phone") is not None and reloaded.calibration("webcam") is None
    assert run(reloaded, H0).trustworthy
    assert reloaded.process("webcam", frame(WORLD_BLOCK, H0), 0).bypass  # the laptop is uncalibrated

    (folder / "phone.json").write_text("{not json")
    broken = MatService(CFG, folder)
    r = broken.process("phone", frame(WORLD_BLOCK, H0), 0)
    assert r.state == "recalibrate" and not r.bypass and r.canonical is None and not r.trustworthy

    svc.calibrate("phone", frame(WORLD_BLOCK, H0), normalized_landmarks(H0))
    (folder / "phone.png").unlink()
    r = MatService(CFG, folder).process("phone", frame(WORLD_BLOCK, H0), 0)
    assert r.state == "recalibrate" and not r.bypass

    data = json.loads((folder / "phone.json").read_text())
    data["points"] = [data["points"][i] for i in (0, 2, 1, 3)]  # crossed
    (folder / "phone.json").write_text(json.dumps(data))
    assert MatService(CFG, folder).process("phone", frame(WORLD_BLOCK, H0), 0).state == "recalibrate"

    svc.clear("phone")
    assert not (folder / "phone.json").exists()
    assert MatService(CFG, folder).process("phone", frame(WORLD_BLOCK, H0), 0).bypass


def test_simulator_bypasses_the_mat():
    svc = calibrated()
    r = svc.process("sim", frame(WORLD_BLOCK, H0), 0)
    assert r.bypass and r.state == "off" and svc.status("sim")["state"] == "off"


def test_status_reports_normalized_corners_without_internal_arrays():
    svc = calibrated()
    run(svc, H0)
    status = svc.status("phone")
    assert status["state"] == "tracking" and status["trustworthy"] is True
    assert np.array(status["corners"]) == pytest.approx(np.array(normalized_landmarks(H0)), abs=0.003)
    json.dumps(status)  # serializable
    assert mat_point(0, 0) == (190, 190)
