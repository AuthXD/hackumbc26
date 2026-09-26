from app.config import VisionConfig
from app.vision import MotionMeter, detect_objects

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
