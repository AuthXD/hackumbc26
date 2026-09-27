"""Which connected page supplies camera frames, and what the phone is really doing.

Ownership is earned by frames, not by connecting: the newest page that has *delivered a valid frame*
(and is still delivering) owns the camera. A phone that has connected but not yet produced a frame never
takes over, and is never reported as streaming. When the owner disconnects or stops sending, ownership
falls back and the laptop is told to resume its own camera.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Hashable, Literal

Role = Literal["laptop", "phone"]
Kind = Literal["webcam", "sim", "phone"]
PhoneState = Literal["disconnected", "connected", "streaming", "error"]

JPEG_SOI = b"\xff\xd8\xff"


def looks_like_jpeg(data: bytes) -> bool:
    return len(data) > 128 and data.startswith(JPEG_SOI)


@dataclass
class SourceClient:
    key: Hashable
    order: int  # connection order; larger = newer
    role: Role = "laptop"
    kind: Kind = "webcam"
    streaming_since: float | None = None  # start of the current run of valid frames
    last_frame_at: float | None = None
    frames: int = 0
    camera_error: str = ""
    extras: dict = field(default_factory=dict)


class SourceRegistry:
    def __init__(self, stale_after: float = 2.5) -> None:
        self.stale_after = stale_after  # seconds without a frame before a source stops owning the camera
        self._clients: dict[Hashable, SourceClient] = {}
        self._order = 0

    # -- membership ---------------------------------------------------------------------------------

    def connect(self, key: Hashable) -> SourceClient:
        self._order += 1
        client = SourceClient(key=key, order=self._order)
        self._clients[key] = client
        return client

    def disconnect(self, key: Hashable) -> None:
        self._clients.pop(key, None)

    def hello(self, key: Hashable, role: str, kind: str | None = None) -> None:
        client = self._clients.get(key)
        if client is None:
            return
        client.role = "phone" if role == "phone" else "laptop"
        client.kind = "phone" if client.role == "phone" else ("sim" if kind == "sim" else "webcam")

    def set_kind(self, key: Hashable, kind: str) -> None:
        client = self._clients.get(key)
        if client is not None and client.role == "laptop" and kind in ("webcam", "sim"):
            if client.kind != kind:  # a different picture: its frames start a new run
                client.kind = kind  # type: ignore[assignment]
                client.streaming_since = None

    def camera_status(self, key: Hashable, state: str, message: str = "") -> None:
        client = self._clients.get(key)
        if client is None:
            return
        if state == "error":
            client.camera_error = (message or "Camera unavailable")[:120]
            client.streaming_since = None
        elif state in ("starting", "ready"):
            client.camera_error = ""

    # -- frames -------------------------------------------------------------------------------------

    def _fresh(self, client: SourceClient, now: float) -> bool:
        return client.last_frame_at is not None and now - client.last_frame_at <= self.stale_after

    def frame_received(self, key: Hashable, data: bytes, now: float) -> bool:
        """Record a frame. True when this frame comes from the owner and should be processed."""
        client = self._clients.get(key)
        if client is None or not looks_like_jpeg(data):
            return False
        if client.streaming_since is None or not self._fresh(client, now):
            client.streaming_since = now  # a new run: newest valid source takes over
        client.last_frame_at = now
        client.frames += 1
        client.camera_error = ""
        return self.owner(now) is client

    def frame_invalid(self, key: Hashable) -> None:
        """The frame could not be decoded: it does not count as streaming."""
        client = self._clients.get(key)
        if client is not None:
            client.streaming_since = None
            client.last_frame_at = None

    # -- decisions ------------------------------------------------------------------------------------

    def owner(self, now: float) -> SourceClient | None:
        live = [c for c in self._clients.values() if c.streaming_since is not None and self._fresh(c, now)]
        return max(live, key=lambda c: (c.streaming_since, c.order)) if live else None

    def may_stream(self, key: Hashable, now: float) -> bool:
        """Should this page send frames? Owners keep sending; phones may always try to take over;
        a laptop resumes as soon as nobody else is delivering frames."""
        client = self._clients.get(key)
        if client is None:
            return False
        owner = self.owner(now)
        if owner is None or owner is client:
            return True
        return client.role == "phone" and owner.role != "phone"

    def speaker(self) -> Hashable | None:
        """The newest laptop page speaks coaching aloud (never the phone)."""
        laptops = [c for c in self._clients.values() if c.role == "laptop"]
        return max(laptops, key=lambda c: c.order).key if laptops else None

    def phone_state(self, now: float) -> PhoneState:
        phones = [c for c in self._clients.values() if c.role == "phone"]
        if not phones:
            return "disconnected"
        owner = self.owner(now)
        if owner is not None and owner.role == "phone":
            return "streaming"
        if any(c.camera_error for c in phones):
            return "error"
        return "connected"

    def source_kind(self, key: Hashable) -> Kind:
        client = self._clients.get(key)
        return client.kind if client else "webcam"

    def status(self, now: float) -> dict:
        owner = self.owner(now)
        phones = [c for c in self._clients.values() if c.role == "phone"]
        error = next((c.camera_error for c in phones if c.camera_error), "")
        return {
            "owner": owner.kind if owner else "none",
            "phone": self.phone_state(now),
            "phoneError": error,
            "phoneFrames": sum(c.frames for c in phones),
        }

    def client_view(self, key: Hashable, now: float) -> dict:
        """Per-page flags added to every broadcast."""
        owner = self.owner(now)
        return {
            "active": self.may_stream(key, now),  # kept for older pages: "you may send frames"
            "owner": owner is not None and owner.key == key,
            "speaker": self.speaker() == key,
        }
