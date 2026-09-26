from app.config import VisionConfig
from app.models import SceneState
from app.vision import MotionMeter, color_from_sample, detect_objects, sample_hsv, suppress_static_stacks

from .synthetic import SKIN, as_jpeg_roundtrip, blank, draw_block, zone_center


def by_id(objs):
    return {o.id: o for o in objs}


def test_detects_four_colors_in_their_zones():
    img = blank()
    draw_block(img, "red", zone_center("A", 0))
    draw_block(img, "blue", zone_center("A", 2))
    draw_block(img, "yellow", zone_center("B", 1))
    draw_block(img, "green", zone_center("C", 3))
    objs = by_id(detect_objects(as_jpeg_roundtrip(img), VisionConfig()))
    assert set(objs) == {"red", "blue", "yellow", "green"}
    assert {k: o.zone for k, o in objs.items()} == {"red": "A", "blue": "A", "yellow": "B", "green": "C"}
    assert all(o.stacked_on is None for o in objs.values())
    assert all(o.confidence > 0.8 for o in objs.values())


def test_object_outside_all_zones_has_no_zone():
    img = blank()
    draw_block(img, "red", (320, 20), half=15)  # above the zone band
    objs = by_id(detect_objects(img, VisionConfig()))
    assert objs["red"].zone is None


def test_small_block_on_large_block_is_stacked_and_inherits_zone():
    img = blank()
    base = zone_center("B", 1)
    draw_block(img, "blue", base, half=45)
    draw_block(img, "red", (base[0] + 4, base[1] - 6), half=22)
    objs = by_id(detect_objects(as_jpeg_roundtrip(img), VisionConfig()))
    assert objs["red"].stacked_on == "blue"
    assert objs["blue"].stacked_on is None
    assert objs["red"].zone == "B"


def test_angled_view_stack_top_object_sits_higher_in_image():
    img = blank()
    x, y = zone_center("C", 2)
    draw_block(img, "green", (x, y), half=30)
    draw_block(img, "yellow", (x, y - 40), half=30)  # overlaps upper part of green
    objs = by_id(detect_objects(img, VisionConfig()))
    assert objs["yellow"].stacked_on == "green"
    assert objs["green"].stacked_on is None


def test_adjacent_blocks_are_not_stacked():
    img = blank()
    x, y = zone_center("A", 1)
    draw_block(img, "red", (x - 30, y), half=28)
    draw_block(img, "blue", (x + 30, y), half=28)  # edges touch, boxes barely overlap
    objs = by_id(detect_objects(img, VisionConfig()))
    assert objs["red"].stacked_on is None and objs["blue"].stacked_on is None


def test_skin_tone_and_tiny_specks_are_ignored():
    img = blank()
    draw_block(img, "red", zone_center("A", 0), half=60, bgr=SKIN)  # a "hand"
    draw_block(img, "blue", zone_center("B", 0), half=3)  # speck
    assert detect_objects(as_jpeg_roundtrip(img), VisionConfig()) == []


def test_motion_meter_is_low_for_still_frames_and_high_for_moving_hand():
    img = blank()
    draw_block(img, "red", zone_center("A", 0))
    meter = MotionMeter()
    meter.update(img)
    assert meter.update(img.copy()) < 0.1
    moved = img.copy()
    draw_block(moved, "red", zone_center("B", 0), half=80, bgr=SKIN)
    assert meter.update(moved) > 1.5


def test_new_tower_touching_a_stationary_object_does_not_stack_it():
    cfg = VisionConfig()
    x, y = zone_center("A", 2)
    before = blank()
    draw_block(before, "green", (x, y - 70), half=28)
    draw_block(before, "blue", (x, y + 40), half=28)
    draw_block(before, "yellow", zone_center("B", 1), half=28)
    ref = SceneState(objects=detect_objects(before, cfg))
    after = blank()
    draw_block(after, "green", (x, y - 70), half=28)  # did not move
    draw_block(after, "blue", (x, y + 40), half=28)
    draw_block(after, "yellow", (x, y - 5), half=28)  # stacked on blue; its top now touches green
    raw = {o.id: o for o in detect_objects(after, cfg)}
    assert raw["green"].stacked_on == "yellow"  # the 2D ambiguity this filter exists for
    fixed = {o.id: o for o in suppress_static_stacks(SceneState(objects=list(raw.values())), ref, cfg).objects}
    assert fixed["green"].stacked_on is None and fixed["green"].zone == "A"
    assert fixed["yellow"].stacked_on == "blue"


def test_click_calibration_recovers_an_off_default_color():
    cfg = VisionConfig()
    img = blank()
    draw_block(img, "red", zone_center("B", 1), half=30, bgr=(110, 110, 190))  # washed-out red under harsh light (S≈107)
    assert "red" not in {o.id for o in detect_objects(img, cfg)}
    cx, cy = zone_center("B", 1)
    hsv = sample_hsv(img, cx / img.shape[1], cy / img.shape[0])
    cfg.colors[0] = color_from_sample("red", "#f00", hsv)
    found = {o.id: o for o in detect_objects(img, cfg)}
    assert found["red"].zone == "B"


def test_calibration_band_wraps_around_hue_zero():
    band = color_from_sample("red", "#f00", (3, 200, 200)).bands
    assert len(band) == 2 and band[0][0][0] == 0 and band[1][1][0] == 179
