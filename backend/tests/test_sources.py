"""Camera ownership is earned by valid frames; phone status is never claimed before frames arrive."""

from app.sources import SourceRegistry, looks_like_jpeg

from .test_session import START, frame

JPEG = frame(START)


def registry():
    r = SourceRegistry(stale_after=2.0)
    r.connect("laptop")
    r.hello("laptop", "laptop", "webcam")
    return r


def test_connected_phone_is_not_streaming_and_does_not_own_the_camera():
    r = registry()
    assert r.frame_received("laptop", JPEG, 0.0)
    r.connect("phone")
    r.hello("phone", "phone")
    assert r.phone_state(0.1) == "connected"
    assert r.owner(0.1).key == "laptop" and r.may_stream("laptop", 0.1)
    assert r.may_stream("phone", 0.1)  # the phone may try to take over...
    assert r.frame_received("phone", JPEG, 0.2)  # ...and owns the camera from its first valid frame
    assert r.phone_state(0.2) == "streaming" and not r.may_stream("laptop", 0.2)
    assert not r.frame_received("laptop", JPEG, 0.3)  # a late laptop frame is not processed


def test_invalid_frames_never_count_as_streaming():
    r = registry()
    r.connect("phone")
    r.hello("phone", "phone")
    assert not looks_like_jpeg(b"\xff\xd8")
    assert not r.frame_received("phone", b"nope" * 100, 0.0)
    assert r.phone_state(0.0) == "connected"
    assert r.frame_received("phone", JPEG, 0.1)
    r.frame_invalid("phone")  # the decoder rejected it
    assert r.phone_state(0.2) == "connected" and r.owner(0.2) is None


def test_camera_error_is_reported_and_clears_on_frames():
    r = registry()
    r.connect("phone")
    r.hello("phone", "phone")
    r.camera_status("phone", "error", "NotAllowedError: permission denied")
    assert r.phone_state(0.0) == "error"
    assert r.status(0.0)["phoneError"].startswith("NotAllowedError")
    r.frame_received("phone", JPEG, 0.5)
    assert r.phone_state(0.5) == "streaming" and r.status(0.5)["phoneError"] == ""


def test_disconnect_or_stall_returns_the_camera_to_the_laptop():
    r = registry()
    r.connect("phone")
    r.hello("phone", "phone")
    r.frame_received("phone", JPEG, 0.0)
    assert not r.may_stream("laptop", 1.0)
    assert r.may_stream("laptop", 3.0) and r.phone_state(3.0) == "connected"  # stalled phone: laptop resumes
    r.frame_received("phone", JPEG, 3.1)  # phone resumes: newest run owns again
    assert r.owner(3.1).key == "phone"
    r.disconnect("phone")
    assert r.phone_state(3.2) == "disconnected" and r.may_stream("laptop", 3.2) and r.owner(3.2) is None


def test_newest_streaming_source_wins_and_only_laptops_speak():
    r = registry()
    r.connect("phone-a")
    r.hello("phone-a", "phone")
    r.connect("phone-b")
    r.hello("phone-b", "phone")
    r.frame_received("phone-a", JPEG, 0.0)
    r.frame_received("phone-b", JPEG, 0.1)
    assert r.owner(0.2).key == "phone-b"
    assert not r.may_stream("phone-a", 0.2)  # an older phone waits while a newer one streams
    assert r.speaker() == "laptop"
    assert r.client_view("phone-b", 0.2) == {"active": True, "owner": True, "speaker": False}


def test_switching_laptop_source_starts_a_new_run():
    r = registry()
    r.frame_received("laptop", JPEG, 0.0)
    r.set_kind("laptop", "sim")
    assert r.source_kind("laptop") == "sim" and r.owner(0.1) is None
    r.frame_received("laptop", JPEG, 0.2)
    assert r.status(0.2)["owner"] == "sim"
