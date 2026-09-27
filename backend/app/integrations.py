"""Optional cloud integrations. Both are strictly additive: any failure returns None and the demo
carries on with deterministic step text and browser speech.

Gemini only *words* a transition that the deterministic engine already detected. It never decides
whether a physical step was performed correctly.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from collections import OrderedDict

import httpx

from .assistant import (
    ASK_SCHEMA,
    METADATA_SCHEMA,
    SYSTEM_INSTRUCTION,
    AskAnswer,
    ProcedureContext,
    ask_prompt,
    fallback_answer,
    metadata_prompt,
    validate_answer,
    validate_metadata,
)
from .config import Settings
from .library import ProcedureMetadata
from .models import LearnedStep, StepText

log = logging.getLogger("teachback.integrations")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}"
# Assistant calls: thinking tokens count against the output cap, and live latency was ~20-25 s under load.
ASSISTANT_TIMEOUT_S = 30
ASSISTANT_MAX_TOKENS = 2048

STEP_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING", "description": "Short step title, at most 6 words."},
        "instruction": {"type": "STRING", "description": "One clear imperative sentence."},
    },
    "required": ["title", "instruction"],
}

PROMPT = """You are naming one step of a short tabletop procedure for a coaching app.
A deterministic vision system already detected exactly what changed. Treat the structured change as ground truth;
the two photos (before, then after) are only context. Do not add objects, zones, or actions that are not in the change.
Refer to objects by color (e.g. "the red block") and to zones by their labels.

Structured change (placements are zone ids or "stackedOn" another object):
{delta}

Deterministic description: "{fallback}"

Return JSON with:
- "title": a short step title (max 6 words)
- "instruction": one clear imperative sentence a newcomer could follow."""


def _primary_objects(step: LearnedStep) -> list[str]:
    """Objects that were actually handled (not just carried on top of something that moved)."""
    out = []
    for oid in step.delta.changed_ids:
        b, a = step.delta.before.get(oid), step.delta.after.get(oid)
        if b and a and b.stacked_on and b.stacked_on == a.stacked_on:
            continue
        out.append(oid)
    return out or step.delta.changed_ids


def validate_step_text(raw: str, step: LearnedStep) -> StepText | None:
    """Strict JSON with two short strings that mention every handled object's color."""
    try:
        data = json.loads(raw)
        text = StepText(title=str(data["title"]).strip(), instruction=str(data["instruction"]).strip())
    except (ValueError, KeyError, TypeError):
        return None
    if not text.title or not text.instruction or len(text.title) > 60 or len(text.instruction) > 220:
        return None
    blob = f"{text.title} {text.instruction}".lower()
    if not all(oid.lower() in blob for oid in _primary_objects(step)):
        return None  # wording that drops a handled object is misleading; keep the deterministic text
    return text


class GeminiDescriber:
    def __init__(self, cfg: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.cfg = cfg
        self.transport = transport  # injectable for offline tests
        self.cache: dict[str, StepText] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.gemini_api_key)

    @staticmethod
    def cache_key(step: LearnedStep) -> str:
        payload = json.dumps(step.delta.to_json(), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    async def describe(self, step: LearnedStep, before: bytes | None, after: bytes | None) -> StepText | None:
        if not self.enabled:
            return None
        key = self.cache_key(step)
        if key in self.cache:
            return self.cache[key]
        parts: list[dict] = [{
            "text": PROMPT.format(
                delta=json.dumps(step.delta.to_json(), indent=1),
                fallback=step.description.instruction,
            )
        }]
        for img in (before, after):
            if img:
                parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(img).decode()}})
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": STEP_SCHEMA,
                "temperature": 0.2,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=15, transport=self.transport) as client:
                res = await client.post(
                    GEMINI_URL.format(model=self.cfg.gemini_model),
                    headers={"x-goog-api-key": self.cfg.gemini_api_key},
                    json=body,
                )
            if res.status_code != 200:
                log.warning("Gemini HTTP %s: %s", res.status_code, res.text[:300])
                return None
            raw = res.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as exc:
            log.warning("Gemini request failed: %s", exc)
            return None
        text = validate_step_text(raw, step)
        if text is None:
            log.warning("Gemini response rejected: %s", raw[:300])
            return None
        self.cache[key] = text
        return text


    # -- procedure assistant (text only, grounded in one procedure) ---------------------------------------

    async def _generate(self, prompt: str, schema: dict) -> str | None:
        """One structured JSON call. Logs only status codes and exception classes: prompts carry user text."""
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "temperature": 0.2,
                "maxOutputTokens": ASSISTANT_MAX_TOKENS,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=ASSISTANT_TIMEOUT_S, transport=self.transport) as client:
                res = await client.post(
                    GEMINI_URL.format(model=self.cfg.gemini_model),
                    headers={"x-goog-api-key": self.cfg.gemini_api_key},
                    json=body,
                )
            if res.status_code != 200:
                log.warning("Gemini assistant HTTP %s", res.status_code)
                return None
            raw = res.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as exc:
            log.warning("Gemini assistant request failed (%s)", type(exc).__name__)
            return None
        return raw if isinstance(raw, str) and len(raw) <= 4000 else None

    async def suggest_metadata(self, ctx: ProcedureContext) -> tuple[str, ProcedureMetadata | None]:
        """("suggested", metadata) only for a live, validated, grounded answer; else ("unavailable"|"rejected", None)."""
        if not self.enabled:
            return "unavailable", None
        raw = await self._generate(metadata_prompt(ctx), METADATA_SCHEMA)
        if raw is None:
            return "unavailable", None
        meta = validate_metadata(raw, ctx)
        if meta is None:
            log.warning("Gemini procedure suggestion rejected (malformed, oversized or ungrounded)")
            return "rejected", None
        return "suggested", meta

    async def ask(self, ctx: ProcedureContext, question: str) -> AskAnswer:
        """A grounded answer, or the stored instructions with an honest notice. Never raises."""
        if not self.enabled:
            return fallback_answer(ctx, question, "Gemini unavailable, showing stored instructions.")
        raw = await self._generate(ask_prompt(ctx, question), ASK_SCHEMA)
        if raw is None:
            return fallback_answer(ctx, question, "Gemini unavailable, showing stored instructions.")
        answer = validate_answer(raw, ctx, question)
        if answer is None:
            log.warning("Gemini answer rejected (malformed or not grounded in the procedure)")
            return fallback_answer(ctx, question,
                                   "Gemini's answer was not grounded in this procedure, showing stored instructions.")
        return answer


class ElevenLabsVoice:
    def __init__(self, cfg: Settings, cache_size: int = 64, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.cfg = cfg
        self.transport = transport
        self.cache: OrderedDict[str, bytes] = OrderedDict()
        self.cache_size = cache_size

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.elevenlabs_api_key)

    async def speak(self, text: str) -> bytes | None:
        if not self.enabled or not text.strip():
            return None
        if text in self.cache:
            self.cache.move_to_end(text)
            return self.cache[text]
        try:
            async with httpx.AsyncClient(timeout=15, transport=self.transport) as client:
                res = await client.post(
                    ELEVENLABS_URL.format(voice=self.cfg.elevenlabs_voice_id),
                    params={"output_format": "mp3_44100_128"},
                    headers={"xi-api-key": self.cfg.elevenlabs_api_key},
                    json={"text": text, "model_id": self.cfg.elevenlabs_model},
                )
            if res.status_code != 200:
                log.warning("ElevenLabs HTTP %s: %s", res.status_code, res.text[:300])
                return None
        except Exception as exc:
            log.warning("ElevenLabs request failed: %s", exc)
            return None
        self.cache[text] = res.content
        if len(self.cache) > self.cache_size:
            self.cache.popitem(last=False)
        return res.content
