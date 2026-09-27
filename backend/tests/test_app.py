"""Smoke tests for the real FastAPI app: startup, websocket frames, commands, calibration."""

from fastapi.testclient import TestClient

from app.main import app
from app.session import Session

from .test_session import START, frame


def test_app_starts_and_websocket_round_trips():
    with TestClient(app) as client:
        assert client.get("/api/health").json()["ok"] is True
        with client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "update" and "mode" in hello
            ws.send_bytes(frame(START))
            update = ws.receive_json()
            assert {o["id"] for o in update["scene"]["objects"]} == {"red", "blue", "yellow", "green"}
            assert update["active"] is True
            ws.send_json({"type": "command", "action": "reset"})
            assert ws.receive_json()["mode"] == "idle"


def test_session_calibration_updates_color_range():
    s = Session(persist=False)
    s.process_frame(frame(START), now=1.0)
    snap = s.calibrate("red", 0.17, 0.28)  # red block's position (zone A, slot 0)
    assert snap["notice"].startswith("Calibrated red")
    assert s.reset_colors()["notice"] == "Colors reset to defaults."


def test_phone_takes_the_camera_only_after_its_first_valid_frame_and_disconnect_returns_it():
    jpeg = frame(START)
    with TestClient(app) as client, client.websocket_connect("/ws") as laptop:
        first = laptop.receive_json()
        assert first["active"] is True and first["camera"]["phone"] == "disconnected"
        with client.websocket_connect("/ws") as phone:
            joined = laptop.receive_json()
            phone.receive_json()
            phone.send_json({"type": "hello", "role": "phone"})
            connected = laptop.receive_json()
            phone.receive_json()
            # Connected, no frames yet: the laptop keeps its camera and nobody claims "streaming".
            assert joined["active"] is True and connected["active"] is True
            assert connected["camera"]["phone"] == "connected" and connected["camera"]["owner"] == "none"

            phone.send_bytes(b"not a jpeg" * 20)  # garbage never takes the camera
            phone.send_json({"type": "camera_status", "state": "error", "message": "NotAllowedError"})
            errored = laptop.receive_json()
            phone.receive_json()
            assert errored["camera"]["phone"] == "error" and errored["active"] is True

            phone.send_bytes(jpeg)
            assert laptop.receive_bytes() == jpeg  # binary JPEG relay to the laptop
            streaming = laptop.receive_json()
            mine = phone.receive_json()
            assert streaming["camera"] == {"owner": "phone", "phone": "streaming", "phoneError": "", "phoneFrames": 1}
            assert streaming["active"] is False and streaming["speaker"] is True
            assert mine["owner"] is True and mine["speaker"] is False
            assert {o["id"] for o in streaming["scene"]["objects"]} == {"red", "blue", "yellow", "green"}

        back = laptop.receive_json()
        assert back["active"] is True and back["camera"]["phone"] == "disconnected"
        assert back["camera"]["owner"] == "none"


def test_speak_without_key_returns_503_so_browser_falls_back(monkeypatch):
    from app import main

    monkeypatch.setattr(main.voice.cfg, "elevenlabs_api_key", "")
    with TestClient(app) as client:
        assert client.post("/api/speak", json={"text": "hello"}).status_code == 503
