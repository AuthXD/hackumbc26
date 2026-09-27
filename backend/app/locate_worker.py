"""Lazy, resident LocateAnything process. No model dependencies enter the web server."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

from .config import DATA_DIR, REPO_ROOT, Settings


def linux_path(path: Path) -> str:
    path = path.resolve()
    if os.name == "nt":
        return "/mnt/" + path.drive[0].lower() + path.as_posix()[2:]
    return str(path)


class LocateWorker:
    def __init__(self, cfg: Settings, command: list[str] | None = None) -> None:
        self.cfg = cfg
        self.command = command
        self.state = "unloaded"
        self.process = None
        self.pid = None
        self.log = None
        self.log_path = DATA_DIR / "locate-worker.log"
        self.reader = None
        self.lifecycle = threading.RLock()
        self.closed = False
        self.sequence = 0

    def _command(self) -> list[str]:
        if self.command is not None:
            return self.command
        library = self.cfg.locate_library
        if not library.startswith("/"):
            library = linux_path(Path(library))
        worker = REPO_ROOT / "benchmarks/locate_anything/worker.py"
        command = ["env", "LA_DEVICE=CUDA0", "python3", linux_path(worker),
                   "--library", library, "--model", self.cfg.locate_model]
        return ["wsl", "-d", self.cfg.locate_distro, "--", *command] if os.name == "nt" else command

    @staticmethod
    def _read(process, messages):
        try:
            for line in process.stdout:
                messages.put(json.loads(line))
        except (ValueError, OSError) as exc:
            messages.put({"type": "protocol_error", "error": str(exc)})
        finally:
            messages.put({"type": "eof"})

    def _receive(self, timeout):
        try:
            reply = self.messages.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"LocateAnything exceeded its {timeout:g}s deadline") from None
        if reply.get("type") in ("eof", "protocol_error"):
            raise RuntimeError("LocateAnything worker stopped. Check locate-worker.log.")
        if reply.get("type") == "starting":
            self.pid = int(reply["pid"])
        return reply

    def _diagnostics(self):
        text = self.log_path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"out of memory|CUDA error|failed|GGML_ASSERT|aborted", text, re.I):
            raise RuntimeError("LocateAnything reported a native failure. Check locate-worker.log.")
        if self.command is None and "using device: CUDA0" not in text:
            raise RuntimeError("LocateAnything CUDA device is not ready")

    def _start(self):
        with self.lifecycle:
            if self.closed:
                raise RuntimeError("LocateAnything worker is closed")
            if self.process is not None:
                return
            self.state = "loading"
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log = self.log_path.open("w", encoding="utf-8")
            self.messages = queue.Queue()
            self.process = subprocess.Popen(self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=self.log, text=True, encoding="utf-8", bufsize=1)
            self.reader = threading.Thread(target=self._read, args=(self.process, self.messages), daemon=True)
            self.reader.start()
        deadline = time.monotonic() + self.cfg.locate_startup_timeout
        reply = self._receive(self.cfg.locate_startup_timeout)
        if reply.get("type") == "starting":
            reply = self._receive(max(.001, deadline - time.monotonic()))
        if reply.get("type") != "ready":
            raise RuntimeError("LocateAnything did not become ready")
        self._diagnostics()
        self.state = "ready"

    def predict(self, image: Path, labels: tuple[str, ...]) -> list[dict]:
        try:
            self._start()
            self.sequence += 1
            process = self.process
            if process is None:
                raise RuntimeError("LocateAnything worker stopped")
            process.stdin.write(json.dumps({"id": self.sequence, "image": linux_path(image), "labels": labels}) + "\n")
            process.stdin.flush()
            reply = self._receive(self.cfg.locate_request_timeout)
            if reply.get("type") != "result" or reply.get("id") != self.sequence or reply.get("status") != "ok":
                raise RuntimeError(reply.get("error", "Invalid LocateAnything response"))
            self._diagnostics()
            return reply["detections"]
        except Exception:
            self.state = "error"
            self._terminate()
            raise

    def _terminate(self):
        with self.lifecycle:
            process, self.process = self.process, None
            pid, self.pid = self.pid, None
            if process is not None:
                if pid and process.poll() is None:
                    command = ["kill", "-TERM", str(pid)]
                    if os.name == "nt":
                        command = ["wsl", "-d", self.cfg.locate_distro, "--", *command]
                    try:
                        subprocess.run(command, capture_output=True, timeout=5)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                try:
                    process.stdin.close()
                except OSError:
                    pass
                if self.reader:
                    self.reader.join(timeout=2)
                process.stdout.close()
            if self.log:
                self.log.close()
                self.log = None

    def close(self):
        with self.lifecycle:
            self.closed = True
        self._terminate()
