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


def test_newest_camera_relays_frames_and_disconnect_returns_ownership():
    jpeg = frame(START)
    with TestClient(app) as client, client.websocket_connect("/ws") as laptop:
        assert laptop.receive_json()["active"] is True
        with client.websocket_connect("/ws") as phone:
            assert laptop.receive_json()["active"] is False
            assert phone.receive_json()["active"] is True

            phone.send_bytes(jpeg)
            assert laptop.receive_bytes() == jpeg
            assert laptop.receive_json()["active"] is False
            assert phone.receive_json()["active"] is True

        assert laptop.receive_json()["active"] is True


def test_speak_without_key_returns_503_so_browser_falls_back(monkeypatch):
    from app import main

    monkeypatch.setattr(main.voice.cfg, "elevenlabs_api_key", "")
    with TestClient(app) as client:
        assert client.post("/api/speak", json={"text": "hello"}).status_code == 503
