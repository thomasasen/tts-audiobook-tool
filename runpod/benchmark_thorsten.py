#!/usr/bin/env python3
"""Model-backed Thorsten CosyVoice3 throughput benchmark for RunPod L40S.

Standard-library launcher; no installs, downloads, edits to production jobs,
or book-text telemetry. This is a controlled synth/WAV/validation benchmark,
not a complete Studio/ASR/chapter-export end-to-end measurement.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "tts_audiobook_tool" / "web_studio" / "thorsten_worker.py"
DATA = Path("/workspace/audiobook-data")
TEXTS = {
    "short": "Guten Tag. Wir sprechen heute über eine klare und nachvollziehbare Vertriebsstrategie.",
    "medium": (
        "In einem komplexen Vertriebsprojekt sollten die Beteiligten zunächst die "
        "geschäftlichen Ziele festhalten. Danach klären sie den Entscheidungsprozess, "
        "die Verantwortlichkeiten und den möglichen Nutzen. Wer diese Schritte "
        "sauber dokumentiert, erkennt Risiken frühzeitig und kann Entscheidungen "
        "besser vorbereiten."
    ),
    "long": (
        "Eine belastbare Kundenstrategie beginnt mit einem gemeinsamen Verständnis "
        "der Ausgangssituation. Das Unternehmen untersucht seine derzeitigen Abläufe, "
        "die beteiligten Rollen und die Erwartungen an künftige Ergebnisse. "
        "Dabei helfen konkrete Beispiele, eine klare Begriffswahl und die konsequente "
        "Unterscheidung zwischen einer Vermutung und einer bestätigten Tatsache. "
        "Anschließend werden mögliche Lösungen anhand nachvollziehbarer Kriterien "
        "bewertet. Dazu zählen der wirtschaftliche Nutzen, die technischen Risiken, "
        "der Aufwand für die Einführung und die Frage, wer eine Entscheidung "
        "tatsächlich treffen darf. Ein realistischer Zeitplan berücksichtigt "
        "Abhängigkeiten, verfügbare Ressourcen und notwendige Abstimmungen. "
        "Ein gutes Fachbuch vermittelt diese Zusammenhänge in verständlicher Sprache "
        "und mit einer ruhigen, gleichmäßigen Stimme."
    ),
}


def _message(proc: subprocess.Popen, timeout: float) -> dict:
    if proc.stdout is None:
        raise RuntimeError("Worker stdout not available")
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ)
        if not selector.select(timeout):
            raise TimeoutError("Thorsten worker did not respond in time")
    line = proc.stdout.readline()
    if not line or len(line) > 20_000:
        raise RuntimeError("Thorsten worker closed or returned oversized response")
    response = json.loads(line)
    if not isinstance(response, dict):
        raise RuntimeError("Invalid Thorsten worker message")
    return response


def _validate_wave(path: Path) -> tuple[float, str]:
    """Validate full PCM payload and calculate its SHA-256 outside TTS timing."""
    sha = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(block)
    with wave.open(str(path), "rb") as audio:
        rate = audio.getframerate()
        frames = audio.getnframes()
        width = audio.getsampwidth()
        channels = audio.getnchannels()
        if rate < 16000 or width != 2 or channels not in (1, 2) or frames < rate // 4:
            raise ValueError("Invalid PCM16 audio")
        seen, signal = 0, False
        while seen < frames:
            payload = audio.readframes(min(65536, frames - seen))
            if not payload or len(payload) % (width * channels):
                raise ValueError("Truncated PCM audio")
            seen += len(payload) // (width * channels)
            if not signal and any(payload):
                signal = True
        if seen != frames or not signal:
            raise ValueError("Empty or truncated audio")
    return frames / rate, sha.hexdigest()


class GpuSampler:
    """Best-effort whole-device nvidia-smi sampling, not process utilization."""

    def __init__(self, interval: float):
        self.interval = interval
        self.samples: list[tuple[int, int, int]] = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                output = subprocess.run(
                    ["nvidia-smi", "--id=0",
                     "--query-gpu=utilization.gpu,memory.used,memory.total",
                     "--format=csv,noheader,nounits"],
                    text=True, capture_output=True, timeout=3, check=False,
                )
                if output.returncode == 0:
                    fields = output.stdout.strip().splitlines()[0].split(",")
                    if len(fields) == 3:
                        self.samples.append(tuple(int(n.strip()) for n in fields))
            except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
                pass
            self.stop.wait(self.interval)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=5)

    def summary(self) -> dict:
        if not self.samples:
            return {"gpu_utilization_mean_pct": None, "gpu_utilization_peak_pct": None,
                    "device_vram_peak_mib": None, "gpu_samples": 0}
        return {
            "gpu_utilization_mean_pct": round(statistics.mean(x[0] for x in self.samples), 2),
            "gpu_utilization_peak_pct": max(x[0] for x in self.samples),
            "device_vram_peak_mib": max(x[1] for x in self.samples),
            "device_vram_total_mib": max(x[2] for x in self.samples),
            "gpu_samples": len(self.samples),
        }


def _summary(rows: list[dict], elapsed: float) -> dict:
    audio = sum(row["audio_seconds"] for row in rows)
    inference = sum(row["inference_seconds"] for row in rows)
    valid = sum(row["validation_seconds"] for row in rows)
    wav = sum(row["wav_seconds"] for row in rows)
    return {
        "segments": len(rows),
        "audio_seconds": round(audio, 4),
        "elapsed_seconds": round(elapsed, 4),
        "inference_seconds": round(inference, 4),
        "wav_seconds": round(wav, 4),
        "validation_seconds": round(valid, 4),
        "inference_rtf": round(inference / audio, 6) if audio else None,
        "pipeline_rtf": round(elapsed / audio, 6) if audio else None,
        "audio_minutes_per_wall_minute": round(audio / elapsed, 4) if elapsed else None,
        "peak_allocated_vram_bytes": max(
            (row["peak_allocated_vram_bytes"] for row in rows
             if row["peak_allocated_vram_bytes"] is not None), default=None
        ),
        "profiles": {
            label: {
                "count": len([x for x in rows if x["profile"] == label]),
                "inference_rtf": round(
                    sum(x["inference_seconds"] for x in rows if x["profile"] == label) /
                    sum(x["audio_seconds"] for x in rows if x["profile"] == label), 6
                ),
            }
            for label in TEXTS if any(x["profile"] == label for x in rows)
        },
    }


def benchmark(args) -> dict:
    python = Path(args.python)
    model_root = Path(args.model_root)
    if not python.is_file() or not WORKER.is_file():
        raise FileNotFoundError("Pinned Thorsten Python or repository worker missing")
    if not (model_root / ".studio-ready").is_file():
        raise FileNotFoundError("Verified model release marker missing; existing RunPod install required")
    if not (model_root / "pretrained_models" / "CosyVoice3-0.5B").is_dir():
        raise FileNotFoundError("Pinned CosyVoice3 model directory missing")
    profiles = list(TEXTS) if args.profile == "all" else [args.profile]
    modes = ["uncached", "cached"] if args.mode == "compare" else [args.mode]
    entries: dict[str, list[dict]] = {mode: [] for mode in modes}
    elapsed: dict[str, float] = {mode: 0 for mode in modes}
    report = {
        "schema": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "gpu": "real device required by Thorsten CUDA preflight",
        "model": "Thorsten-Voice/CosyVoice3 SFT",
        "profiles": profiles,
        "speed": args.speed,
        "warmup_requests": args.warmup,
        "repeats": args.repeats,
        "mode": args.mode,
        "type": "synthesis-plus-PCM-validation; excludes ASR, checkpoints, chapter M4A, M4B",
    }
    # Only synthetic text, volatile WAVs and transient subprocesses.
    with tempfile.TemporaryDirectory(prefix="thorsten-bench-") as temp:
        with open(Path(temp) / "worker.stderr", "w", encoding="utf-8") as errors:
            launched = time.perf_counter()
            proc = subprocess.Popen(
                [str(python), "-u", str(WORKER)],
                cwd=str(model_root), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=errors, text=True, bufsize=1,
            )
            try:
                ready = _message(proc, 300)
                if ready.get("ready") is not True:
                    raise RuntimeError("Thorsten CUDA preflight/model load did not succeed")
                report["model_start_seconds"] = round(time.perf_counter() - launched, 4)
                sequence = 0

                def synth(profile: str, mode: str, *, collect: bool) -> None:
                    nonlocal sequence
                    sequence += 1
                    output = Path(temp) / f"{sequence:05d}.wav"
                    request = {
                        "id": str(sequence),
                        "text": TEXTS[profile],
                        "output": str(output),
                        "speed": args.speed,
                        "benchmark": True,
                        "uncached_fixed_prompt": mode == "uncached",
                    }
                    if proc.stdin is None:
                        raise RuntimeError("Worker stdin missing")
                    started = time.perf_counter()
                    proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                    proc.stdin.flush()
                    result = _message(proc, 600)
                    if result.get("id") != request["id"] or result.get("ok") is not True:
                        raise RuntimeError("Thorsten synthesis failed")
                    timed = time.perf_counter()
                    try:
                        duration, _ = _validate_wave(output)
                    finally:
                        output.unlink(missing_ok=True)
                    finished = time.perf_counter()
                    if collect:
                        metrics = result.get("metrics", {})
                        if not (isinstance(metrics, dict) and
                                isinstance(metrics.get("inference_seconds"), (int, float)) and
                                isinstance(metrics.get("wav_seconds"), (int, float))):
                            raise RuntimeError("Worker does not provide benchmark timings")
                        entries[mode].append({
                            "profile": profile,
                            "audio_seconds": duration,
                            "inference_seconds": metrics["inference_seconds"],
                            "wav_seconds": metrics["wav_seconds"],
                            "validation_seconds": finished - timed,
                            "peak_allocated_vram_bytes": metrics.get("peak_allocated_vram_bytes"),
                        })
                        elapsed[mode] += finished - started

                with GpuSampler(args.sampling_interval) as sampler:
                    # Warm-up is excluded from timed steady-state measurements.
                    for _ in range(args.warmup):
                        synth("short", "cached", collect=False)
                    for repeat in range(args.repeats):
                        # Alternate mode order to reduce warm/cold ordering bias.
                        order = modes if repeat % 2 == 0 else list(reversed(modes))
                        for profile in profiles:
                            for mode in order:
                                synth(profile, mode, collect=True)
                report["device_telemetry"] = sampler.summary()
                report["results"] = {
                    mode: _summary(entries[mode], elapsed[mode]) for mode in modes
                }
                report["comparison"] = None
                if args.mode == "compare":
                    before = report["results"]["uncached"]["inference_rtf"]
                    after = report["results"]["cached"]["inference_rtf"]
                    if before is not None and after is not None and before:
                        report["comparison"] = {
                            "inference_rtf_change_pct": round((after / before - 1) * 100, 3),
                            "note": "Negative means faster; compare also pipeline RTF and audio quality",
                        }
                return report
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=str(DATA / "venvs/thorsten/bin/python"))
    parser.add_argument("--model-root", default=str(DATA / "models/CosyVoice"))
    parser.add_argument("--profile", choices=["all", *TEXTS], default="all")
    parser.add_argument("--speed", choices=[0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15],
                        type=float, default=1.0)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--mode", choices=["cached", "uncached", "compare"], default="compare")
    parser.add_argument("--sampling-interval", type=float, default=0.3)
    parser.add_argument("--output", type=Path, help="Optional secure JSON file")
    args = parser.parse_args()
    if not (1 <= args.repeats <= 20 and 0 <= args.warmup <= 5
            and 0.1 <= args.sampling_interval <= 2):
        parser.error("repeats must be 1..20, warmup 0..5, interval 0.1..2 seconds")
    try:
        result = benchmark(args)
    except (OSError, ValueError, RuntimeError, TimeoutError,
            subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"Benchmark failed ({type(exc).__name__}); private RunPod worker log may help.", file=sys.stderr)
        return 1
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output is not None:
        destination = args.output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.with_name(destination.name + ".partial")
        try:
            with staging.open("x", encoding="utf-8") as f:
                os.chmod(staging, 0o600)
                f.write(rendered + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(staging, destination)
        finally:
            staging.unlink(missing_ok=True)
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
