"""npm run mat:check uses a repo fixture in CI; the Downloads photo is only a local default."""
import json

import cv2
import numpy as np

from mat_check import NAMED_ORDER, load_fixture, main
from tests.synthetic_mat import FRAME, base_homography, frame, landmarks_in_frame, world

H0 = base_homography()


def _write_fixture(path, points, width, height, order=None):
    items = order or [
        {"id": sid, "name": name, "x": float(x), "y": float(y)}
        for (sid, name), (x, y) in zip(NAMED_ORDER, points)
    ]
    path.write_text(json.dumps({"image_width": width, "image_height": height, "order": items}), encoding="utf-8")


def test_mat_check_succeeds_on_synthetic_fixture(tmp_path):
    image = frame(world(), H0)
    src = tmp_path / "mat.png"
    assert cv2.imwrite(str(src), image)
    fixture = tmp_path / "landmarks.json"
    _write_fixture(fixture, landmarks_in_frame(H0), FRAME[0], FRAME[1])
    out = tmp_path / "results"
    assert main(["--image", str(src), "--fixture", str(fixture), "--output", str(out)]) == 0
    report = json.loads((out / "mat-check.json").read_text(encoding="utf-8"))
    assert report["ok"] and report["quad_valid"]
    assert report["source_width"] == FRAME[0] and report["canonical_height"] > 0
    assert (out / "annotated-source.png").is_file()
    assert (out / "canonical.png").is_file()
    assert (out / "canonical-band.png").is_file()


def test_invalid_mat_fixture_exits_nonzero(tmp_path):
    image = frame(world(), H0)
    src = tmp_path / "mat.png"
    assert cv2.imwrite(str(src), image)
    pts = landmarks_in_frame(H0)
    crossed = pts[[0, 2, 1, 3]]
    fixture = tmp_path / "bad.json"
    _write_fixture(fixture, crossed, FRAME[0], FRAME[1])
    assert main(["--image", str(src), "--fixture", str(fixture), "--output", str(tmp_path / "out")]) == 2
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    assert main(["--image", str(src), "--fixture", str(empty), "--output", str(tmp_path / "out2")]) == 2


def test_missing_source_image_exits_nonzero(tmp_path):
    fixture = tmp_path / "landmarks.json"
    _write_fixture(fixture, np.array([[10.0, 10.0], [90.0, 10.0], [90.0, 90.0], [10.0, 90.0]]), 100, 100)
    assert main(["--image", str(tmp_path / "missing.jpg"), "--fixture", str(fixture),
                 "--output", str(tmp_path / "out")]) == 2


def test_fixture_order_is_tl_tr_br_bl(tmp_path):
    fixture = tmp_path / "order.json"
    _write_fixture(fixture, landmarks_in_frame(H0), FRAME[0], FRAME[1],
                   order=[{"id": "TR", "name": "frog", "x": 1, "y": 1}] * 4)
    try:
        load_fixture(fixture)
        raise AssertionError("expected invalid order")
    except Exception as exc:
        assert "TL" in str(exc)
