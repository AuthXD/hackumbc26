"""Check a real saved keyframe against a DISPOSABLE beta-enabled server; resets its procedure."""
import argparse
import asyncio
import json
from pathlib import Path
import time

import cv2
import websockets

from app.config import Settings
from app.detectors import DEFAULT_LABELS
from demo_check import Demo, render


async def check(args):
    image = cv2.imread(str(args.image))
    if image is None:
        raise ValueError(f"Cannot read {args.image}")
    ratio = min(1, 640 / max(image.shape[:2]))
    image = cv2.resize(image, (round(image.shape[1] * ratio), round(image.shape[0] * ratio)))
    jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()
    async with websockets.connect(args.url, max_size=None) as ws:
        d = Demo(ws)
        await d.recv_update()
        await d.command("reset")
        await ws.send(json.dumps({"type": "detector", "kind": "semantic", "labels": ",".join(DEFAULT_LABELS)}))
        snap = await d.recv_update()
        assert snap["detector"]["kind"] == "semantic", snap["notice"]
        await d.command("teach")
        await d.show(jpeg, 1.6)
        await d.command("scan")
        started = time.monotonic()
        while time.monotonic() - started < 135:
            await d.show(jpeg, .2)
            snap = d.last
            if snap["detector"]["scanState"] in ("valid", "error", "ambiguous"):
                break
        assert snap["detector"]["scanState"] == "valid", snap["detector"]
        objects = snap["scene"]["objects"]
        assert {o["label"] for o in objects} == set(DEFAULT_LABELS)
        for obj in objects:
            assert obj["id"] == obj["label"] and obj["color"] is None and obj["kind"] == "semantic"
            expected_zone = next((z.id for z in Settings().vision.zones if z.contains(*obj["center"])), None)
            assert obj["zone"] == expected_zone
        evidence = {"image": args.image.name, "scan_seconds_including_load": time.monotonic() - started,
                    "detector": snap["detector"], "objects": objects}
        # A direct switch during teaching must fail, then a deliberate pause preserves it.
        await ws.send(json.dumps({"type": "detector", "kind": "color"}))
        assert (await d.recv_update())["detector"]["kind"] == "semantic"
        await d.command("pause")
        await ws.send(json.dumps({"type": "detector", "kind": "color"}))
        switched = await d.recv_update()
        assert switched["detector"]["kind"] == "color"
        assert switched["procedure"]["detectorKind"] == "semantic"
        await d.show(render({"red": "A", "blue": "B", "yellow": "C", "green": "A"}), 1.6)
        assert {o["id"] for o in d.last["scene"]["objects"]} == {"red", "blue", "yellow", "green"}
        evidence["explicit_color_fallback"] = "passed; saved semantic procedure preserved"
        print(json.dumps(evidence, indent=2))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--url", required=True, help="WebSocket URL of a disposable test server")
    parser.add_argument("--output", type=Path)
    asyncio.run(check(parser.parse_args()))
