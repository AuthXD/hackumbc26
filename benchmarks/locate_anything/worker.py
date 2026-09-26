"""Persistent Ubuntu C API worker. JSONL stdin/stdout; native diagnostics stay on stderr."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import struct
import sys
import time

from contract import normalize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--mode", choices=["hybrid", "slow", "fast"], default="hybrid")
    args = parser.parse_args()
    # Preserve protocol output even if C++ writes to stdout.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    try:
        os.setsid()
    except PermissionError:
        pass

    def emit(data):
        protocol.write(json.dumps(data, allow_nan=False) + "\n")

    emit({"type": "starting", "pid": os.getpid()})
    started = time.perf_counter()
    lib = ctypes.CDLL(str(Path(args.library).resolve()))
    lib.la_capi_abi_version.restype = ctypes.c_int
    if lib.la_capi_abi_version() != 1:
        raise RuntimeError("Unsupported LocateAnything C ABI")
    lib.la_capi_load.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.la_capi_load.restype = ctypes.c_void_p
    lib.la_capi_free.argtypes = [ctypes.c_void_p]
    lib.la_capi_locate_path.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    lib.la_capi_locate_path.restype = ctypes.c_void_p
    lib.la_capi_free_string.argtypes = [ctypes.c_void_p]
    lib.la_capi_last_error.argtypes = [ctypes.c_void_p]
    lib.la_capi_last_error.restype = ctypes.c_char_p
    ctx = lib.la_capi_load(os.fsencode(args.model), args.threads)
    if not ctx:
        raise RuntimeError("Model load failed; inspect worker.stderr.log")
    emit({"type": "ready", "load_s": time.perf_counter() - started, "pid": os.getpid(),
          "model": args.model, "mode": args.mode, "confidence_available": False})
    try:
        for line in sys.stdin:
            request = json.loads(line)
            if request.get("type") == "shutdown":
                break
            started = time.perf_counter()
            try:
                path = Path(request["image"])
                with path.open("rb") as f:
                    header = f.read(24)
                if header[:8] != b"\x89PNG\r\n\x1a\n":
                    raise ValueError("Worker accepts prepared PNG keyframes only")
                width, height = struct.unpack(">II", header[16:24])
                prompt = "Locate all the instances that matches the following description: " + "</c>".join(request["labels"]) + "."
                result = lib.la_capi_locate_path(ctx, os.fsencode(path), prompt.encode(),
                                                 {"hybrid": 0, "slow": 1, "fast": 2}[args.mode])
                inference_s = time.perf_counter() - started
                if not result:
                    raise RuntimeError(lib.la_capi_last_error(ctx).decode(errors="replace"))
                try:
                    raw = json.loads(ctypes.string_at(result))
                finally:
                    lib.la_capi_free_string(result)
                emit({"type": "result", "id": request["id"], "status": "ok",
                      "inference_s": inference_s, "raw": raw,
                      "detections": normalize(raw, width, height)})
            except Exception as exc:
                emit({"type": "result", "id": request["id"], "status": "error", "error": str(exc),
                      "inference_s": time.perf_counter() - started})
    finally:
        lib.la_capi_free(ctx)


if __name__ == "__main__":
    main()
