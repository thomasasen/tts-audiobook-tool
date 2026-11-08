"""Opt-in independent Thorsten SFT processes sharing one exclusive Studio GPU.

The production thread remains the only checkpoint/SQLite/chapter writer. This
module only writes uncommitted private *.partial.wav candidates. No EPUB,
glossary, audio caches or source data are modified by subprocess workers.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import json
import os
from pathlib import Path
from queue import Queue
import selectors
import subprocess
import threading
import time
import uuid


def _response(proc: subprocess.Popen, timeout: int) -> dict:
    if proc.stdout is None:
        raise RuntimeError("Thorsten worker stdout unavailable")
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ)
        if not selector.select(timeout):
            raise TimeoutError("Thorsten worker response timeout")
    line = proc.stdout.readline()
    if not line or len(line) > 20_000:
        raise RuntimeError("Invalid Thorsten worker response")
    response = json.loads(line)
    if not isinstance(response, dict):
        raise RuntimeError("Thorsten worker protocol error")
    return response


class _Worker:
    def __init__(self, python: str, model_root: Path, script: Path, log_path: Path):
        self.python = python
        self.model_root = model_root
        self.script = script
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        self.close()
        self.log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.log_path.open("ab") as log:
            os.chmod(self.log_path, 0o600)
            self.proc = subprocess.Popen(
                [self.python, "-u", str(self.script)],
                cwd=str(self.model_root), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=log, text=True, bufsize=1,
            )
        try:
            if _response(self.proc, 300).get("ready") is not True:
                raise RuntimeError("Parallel Thorsten worker initialization failed")
        except Exception:
            self.close()
            raise

    def synthesize(self, text: str, path: Path, speed: float) -> float:
        self.start()
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("Parallel Thorsten worker unavailable")
        request_id = uuid.uuid4().hex
        started = time.perf_counter()
        try:
            proc.stdin.write(json.dumps({
                "id": request_id, "text": text, "output": str(path), "speed": speed,
            }, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            response = _response(proc, 600)
            if response.get("id") != request_id or response.get("ok") is not True:
                raise RuntimeError("Parallel Thorsten worker inference failed")
            return time.perf_counter() - started
        except Exception:
            self.close()
            raise

    def terminate(self) -> None:
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def close(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        finally:
            if proc.stdin:
                proc.stdin.close()
            if proc.stdout:
                proc.stdout.close()


class ParallelWorkerPool:
    """A bounded queue of independent subprocesses; no model shared across threads.

    The owner MUST hold production._GPU and the cross-process Studio lock.
    Only one request at a time is sent to each model process. All completed
    tasks are handed back to the original sequential production writer.
    """

    def __init__(self, config: tuple[str, str], count: int, data_root: Path,
                 *, worker_script: Path | None = None):
        if count not in (2, 3, 4):
            raise ValueError("Parallel Thorsten pool needs 2-4 workers")
        python, official_script = config
        script = worker_script or Path(__file__).with_name("thorsten_worker.py")
        model_root = Path(official_script).parent
        if not Path(python).is_file() or not script.is_file() or not model_root.is_dir():
            raise RuntimeError("Verified local Thorsten installation required")
        log_root = data_root / "logs"
        self._workers = [
            _Worker(python, model_root, script, log_root / f"thorsten-parallel-{i}.log")
            for i in range(count)
        ]
        self._available: Queue[_Worker] = Queue()
        for worker in self._workers:
            self._available.put(worker)
        self.count = count
        self._shutdown = threading.Event()
        self._closed = False
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=count, thread_name_prefix="thorsten-parallel",
        )

    def submit(self, text: str, path: Path, speed: float) -> Future[float]:
        if not isinstance(text, str) or not 1 <= len(text) <= 12000:
            raise ValueError("Invalid narration segment length")
        if not path.is_absolute() or not path.name.endswith(".partial.wav"):
            raise ValueError("Parallel output must be an absolute partial WAV")
        with self._lock:
            if self._closed:
                raise RuntimeError("Parallel Thorsten pool is closed")
            return self._executor.submit(self._run, text, path, speed)

    def _run(self, text: str, path: Path, speed: float) -> float:
        worker = self._available.get()
        try:
            if self._shutdown.is_set():
                raise RuntimeError("Parallel synthesis stopped")
            path.parent.mkdir(parents=True, exist_ok=True)
            return worker.synthesize(text, path, speed)
        finally:
            self._available.put(worker)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._shutdown.set()
        # Stop in-flight work promptly on pause, cancel or shutdown. The
        # production thread never publishes an unfinished partial WAV.
        for worker in self._workers:
            worker.terminate()
        self._executor.shutdown(wait=True, cancel_futures=True)
        for worker in self._workers:
            worker.close()
