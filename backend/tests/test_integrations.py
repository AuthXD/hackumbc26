"""Gemini / ElevenLabs behave as optional add-ons: validated, cached, and fail closed."""

import asyncio
import json

import httpx

from app.config import Settings
from app.integrations import ElevenLabsVoice, GeminiDescriber, validate_step_text

from .helpers import teach

START = dict(red="A", blue="B")


def step():
    return teach(START, dict(red="on:blue")).steps[0]


def gemini_reply(payload) -> httpx.Response:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


def run(coro):
    return asyncio.run(coro)


def settings(**kw) -> Settings:
    s = Settings()
    s.gemini_api_key = kw.get("gemini", "test-key")
    s.elevenlabs_api_key = kw.get("eleven", "test-key")
    return s


def test_validate_accepts_good_json_and_rejects_bad():
    st = step()
    ok = validate_step_text('{"title": "Stack red on blue", "instruction": "Place the red block on the blue block."}', st)
    assert ok and ok.title == "Stack red on blue"
    assert validate_step_text("not json", st) is None
    assert validate_step_text('{"title": "x"}', st) is None
    # Drops the object that was actually handled → rejected as misleading.
    assert validate_step_text('{"title": "Stack", "instruction": "Put the blue block down."}', st) is None
    assert validate_step_text(json.dumps({"title": "t" * 80, "instruction": "red"}), st) is None


def test_gemini_success_is_cached_and_sends_images_and_delta():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(json.loads(req.content))
        assert req.headers["x-goog-api-key"] == "test-key"
        return gemini_reply({"title": "Red onto blue", "instruction": "Stack the red block on the blue block."})

    g = GeminiDescriber(settings(), transport=httpx.MockTransport(handler))
    st = step()
    first = run(g.describe(st, b"before", b"after"))
    second = run(g.describe(st, b"before", b"after"))
    assert first == second and first.title == "Red onto blue"
    assert len(calls) == 1
    parts = calls[0]["contents"][0]["parts"]
    assert "stackedOn" in parts[0]["text"] and len(parts) == 3
    assert calls[0]["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_failures_fall_back_to_none():
    st = step()
    for resp in (httpx.Response(500, text="boom"), gemini_reply("garbage"), httpx.Response(200, json={})):
        g = GeminiDescriber(settings(), transport=httpx.MockTransport(lambda r, resp=resp: resp))
        assert run(g.describe(st, None, None)) is None

    def explode(_):
        raise httpx.ConnectError("offline")

    assert run(GeminiDescriber(settings(), transport=httpx.MockTransport(explode)).describe(st, None, None)) is None
    assert run(GeminiDescriber(settings(gemini="")).describe(st, None, None)) is None
    assert st.description.instruction == "Stack the red object on the blue object."  # deterministic text intact


def test_elevenlabs_returns_audio_caches_and_fails_closed():
    hits = []

    def handler(req: httpx.Request) -> httpx.Response:
        hits.append(req.url.path)
        assert req.headers["xi-api-key"] == "test-key"
        return httpx.Response(200, content=b"ID3mp3")

    v = ElevenLabsVoice(settings(), transport=httpx.MockTransport(handler))
    assert run(v.speak("Step 1 complete.")) == b"ID3mp3"
    assert run(v.speak("Step 1 complete.")) == b"ID3mp3"
    assert len(hits) == 1 and hits[0].startswith("/v1/text-to-speech/")
    bad = ElevenLabsVoice(settings(), transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    assert run(bad.speak("hi")) is None
    assert run(ElevenLabsVoice(settings(eleven="")).speak("hi")) is None
