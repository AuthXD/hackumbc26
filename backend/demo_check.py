"""Plays the judging demo three times against the *running* server (npm run dev), with a different
procedure and a different mistake each time. Frames are synthetic tabletop images sent over the real
WebSocket exactly like the browser does (one in flight, ~5 fps), including a "hand" during each move.

    npm run demo:check          (with the app running)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import cv2
import websockets

from tests.synthetic import SKIN, blank, draw_block, zone_center

URL = os.getenv("TEACHBACK_DEMO_URL", "ws://127.0.0.1:8000/ws")
SLOT = {"red": 0, "blue": 1, "yellow": 2, "green": 3}
FPS = 5


def resolve(layout: dict[str, str]) -> dict[str, tuple[int, int]]:
    """Pixel centers; 'on:x' draws the block on top of x like an angled camera sees a stack."""
    pos: dict[str, tuple[int, int]] = {}

    def place(c: str) -> tuple[int, int]:
        if c not in pos:
            where = layout[c]
            if where.startswith("on:"):
                bx, by = place(where[3:])
                pos[c] = (bx, by - 44)
            else:
                pos[c] = zone_center(where, SLOT[c])
        return pos[c]

    for c in layout:
        place(c)
    return pos


def render(layout: dict[str, str], hand: tuple[int, int] | None = None) -> bytes:
    img = blank()
    pos = resolve(layout)
    depth = lambda c: 1 + depth(layout[c][3:]) if layout[c].startswith("on:") else 0  # noqa: E731
    for c in sorted(layout, key=depth):
        draw_block(img, c, pos[c])
    if hand:
        cv2.ellipse(img, hand, (70, 55), 0, 0, 360, SKIN, -1)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tobytes()


class Demo:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.last: dict = {}
        self.spoken: list[str] = []

    async def recv_update(self) -> dict:
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("type") == "update":
                self.spoken += [e["text"] for e in msg.get("events", [])]
                return msg

    async def command(self, action: str) -> dict:
        await self.ws.send(json.dumps({"type": "command", "action": action}))
        self.last = await self.recv_update()
        return self.last

    async def show(self, jpeg: bytes, seconds: float) -> dict:
        for _ in range(int(seconds * FPS)):
            await self.ws.send(jpeg)
            self.last = await self.recv_update()
            await asyncio.sleep(1 / FPS)
        return self.last

    async def move(self, layout: dict, **change: str) -> dict:
        new = {**layout, **change}
        obj = next(iter(change))
        await self.show(render(layout, resolve(layout)[obj]), 0.6)  # hand reaches in
        await self.show(render(new, resolve(new)[obj]), 0.6)  # hand places it
        await self.show(render(new), 1.6)  # hands off, table settles
        return new


def check(cond: bool, what: str) -> None:
    print(("  PASS " if cond else "  FAIL ") + what)
    if not cond:
        raise SystemExit(1)


RUNS = [
    {
        "name": "moves only, skipped step",
        "start": {"red": "A", "blue": "A", "yellow": "B", "green": "C"},
        "steps": [{"red": "B"}, {"green": "A"}, {"blue": "C"}, {"yellow": "C"}],
        "mistake": {"blue": "C"},  # skips step 2 (green to A)
        "undo": {"blue": "A"},
        "error": "skipped_step",
    },
    {
        "name": "stack + unstack, wrong object",
        "start": {"red": "A", "blue": "B", "yellow": "C", "green": "C"},
        "steps": [{"yellow": "on:blue"}, {"red": "C"}, {"green": "A"}, {"yellow": "A"}],
        "mistake": {"green": "B"},  # nobody moves green to B
        "undo": {"green": "C"},
        "error": "wrong_object",
    },
    {
        "name": "object returns, wrong zone",
        "start": {"red": "A", "blue": "A", "yellow": "B", "green": "C"},
        "steps": [{"blue": "B"}, {"red": "C"}, {"blue": "A"}, {"green": "B"}],
        "mistake": {"red": "B"},  # step 2 is red to C: right object, wrong zone
        "undo": {"red": "C"},  # fixed by moving straight to the right zone
        "error": "wrong_placement",
    },
]


async def run_once(i: int, spec: dict) -> None:
    print(f"\nRun {i + 1}: {spec['name']}")
    async with websockets.connect(URL, max_size=None) as ws:
        d = Demo(ws)
        await d.recv_update()
        await d.command("reset")
        await d.command("teach")
        layout = spec["start"]
        await d.show(render(layout), 1.6)
        for step in spec["steps"]:
            layout = await d.move(layout, **step)
        steps = (d.last.get("procedure") or {}).get("steps", [])
        titles = [s["description"]["title"] for s in steps]
        check(d.last["mode"] == "idle" and len(steps) == 4, f"learned 4 steps: {titles}")

        await d.command("practice")
        await d.show(render(spec["start"]), 1.6)
        check(d.last["practice"]["status"] == "waiting", "starting layout recognized")
        layout = await d.move(spec["start"], **spec["steps"][0])
        check(d.last["practice"]["status"] == "step_complete", "step 1 accepted")

        wrong = await d.move(layout, **spec["mistake"])
        p = d.last["practice"]
        check(p["status"] == "error" and p["errorType"] == spec["error"], f"caught {p['errorType']}: {p['headline']}")
        check(p["expectedStepIndex"] == 1, "did not advance past the error")
        check(any(t.startswith(p["headline"]) for t in d.spoken), "error was spoken")

        layout = await d.move(wrong, **spec["undo"])
        if spec["error"] == "wrong_placement":
            check(d.last["practice"]["status"] == "step_complete", "fixed placement accepted as step 2")
            rest = spec["steps"][2:]
        else:
            check(d.last["practice"]["status"] == "waiting", "undo recognized (back on track)")
            rest = spec["steps"][1:]
        for step in rest:
            layout = await d.move(layout, **step)
        check(d.last["practice"]["status"] == "complete", "procedure complete")


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252
    for i, spec in enumerate(RUNS):
        await run_once(i, spec)
    print("\nAll 3 demo runs passed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except OSError as exc:
        sys.exit(f"Could not reach {URL} - start the app with `npm run dev` first. ({exc})")
