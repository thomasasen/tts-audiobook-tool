"""Persisted, single-GPU German audiobook production pipeline.

This module uses the already-imported Project V5 book.v3 phrase groups. It
does not parse EPUB a second time or mutate the book text. Production is
disabled unless an operator has installed and auditioned a curated voice.
"""
from __future__ import annotations

import hashlib
import logging
import sys
from array import array
import json
import os
import re
import selectors
import subprocess
import threading
import time
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path

from tts_audiobook_tool.web_studio import asr_quality, narration_plan
from tts_audiobook_tool.web_studio.voice_controls import (
    DEFAULT_SETTINGS, verified_settings, verified_speed, verified_preview_text,
)
# Keep the module load lightweight for offline RunPod protocol tests.
DE_V2_NORMALIZER_VERSION = "de-narration-v2"
DE_NORMALIZER_VERSION = "de-narration-v3"

VOICE_ID = "thorsten-cosyvoice3"
SAMPLE_TEXT = "Guten Tag. Dieses Fachbuch erklärt, wie gute Entscheidungen im Vertrieb entstehen."
# Approximately five seconds per sample sentence; individual voices/speeds
# vary. Do not truncate the recording in the middle of spoken words.
SAMPLE_SENTENCES = (
    SAMPLE_TEXT,
    "Wir betrachten dazu konkrete Beispiele aus der Praxis und vergleichen unterschiedliche Vorgehensweisen.",
    "Entscheidend ist, dass alle Beteiligten ihre Ziele verstehen und fundierte Entscheidungen treffen können.",
    "Auch eine gute Vorbereitung hilft dabei, schwierige Gespräche strukturiert und verständlich zu führen.",
    "Ein klares Verständnis für den Kunden ist die Grundlage erfolgreicher Zusammenarbeit über viele Jahre.",
    "Im nächsten Abschnitt geht es um die Frage, wie sich Fortschritte objektiv messen lassen.",
    "Dazu betrachten wir messbare Ergebnisse, typische Hindernisse und die Perspektiven der beteiligten Personen.",
    "Gut geführte Gespräche schaffen Vertrauen, ohne mögliche Probleme oder offene Entscheidungen zu verschweigen.",
    "Die folgenden Beispiele zeigen, weshalb sorgfältiges Nachfragen oft wichtiger ist als schnelle Antworten.",
    "Ein professioneller Vertriebsprozess braucht nachvollziehbare Kriterien und eine verständliche gemeinsame Sprache.",
    "Gerade bei komplexen Projekten sollten Chancen und Risiken regelmäßig geprüft und offen besprochen werden.",
    "Damit endet diese Hörprobe. Beurteile bitte Aussprache, Verständlichkeit, Betonung und die gleichmäßige Sprecherstimme.",
)
SAMPLE_LENGTHS = {5: 1, 15: 3, 30: 6, 60: 12}
MAX_SEGMENTS = 100_000
MAX_GLOSSARY = 500
_WAKE = threading.Event()
_STOP = threading.Event()
_GPU = threading.Lock()
_THREAD: threading.Thread | None = None
_LOCK_FILE = None
_MODEL_PROC = None
_MODEL_READY = False
_WARMUP_ATTEMPTED = False
_WARMUP_STATE = "waiting"
_WARMUP_ACTIVE = threading.Event()
_ASR_MODEL = None
_PREVIEW_LOCK = threading.Lock()
_PREVIEW_STATE: dict = {
    "id": None, "status": "idle", "stage": "Noch keine Hörprobe gestartet.",
    "events": [], "error": "", "duration_seconds": 5,
}
_ACTIVITY_LOCK = threading.Lock()


def _close_model() -> None:
    global _MODEL_PROC, _MODEL_READY
    proc = _MODEL_PROC
    _MODEL_PROC = None
    _MODEL_READY = False
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    finally:
        if proc.stdin:
            proc.stdin.close()
        if proc.stdout:
            proc.stdout.close()


def _model_message(proc, timeout: int) -> dict:
    if proc.stdout is None:
        raise RuntimeError("Thorsten-Protokoll nicht verfügbar.")
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ)
        if not selector.select(timeout):
            raise TimeoutError("Thorsten-Modell antwortet nicht rechtzeitig.")
    line = proc.stdout.readline()
    if not line or len(line) > 20_000:
        raise RuntimeError("Thorsten-Protokoll unterbrochen.")
    data = json.loads(line)
    if not isinstance(data, dict):
        raise RuntimeError("Ungültige Thorsten-Antwort.")
    return data


def _model_loaded() -> bool:
    """Only advertise a live worker after its successful readiness handshake."""
    return bool(_MODEL_READY and _MODEL_PROC is not None and _MODEL_PROC.poll() is None)


def voice_warmup_state() -> str:
    """Safe status only; no paths, model logs, credentials or private text."""
    if os.environ.get("AUDIOBOOK_STUDIO_PRELOAD_VOICE", "1") == "0":
        return "disabled"
    if _model_loaded():
        return "ready"
    if _WARMUP_ACTIVE.is_set():
        return "loading"
    return _WARMUP_STATE if _WARMUP_STATE == "failed" else "waiting"


def _ensure_model_loaded(config: tuple[str, str], on_progress=None) -> None:
    """Start ONE persistent Thorsten worker and wait for a real model handshake.

    Caller must hold _GPU. The exact same worker serves preview and production;
    preloading never launches a disposable second process.
    """
    global _MODEL_PROC, _MODEL_READY
    if _model_loaded():
        return
    if _MODEL_PROC is not None:
        _close_model()
    if on_progress is not None:
        on_progress("Thorsten-Modell wird in den GPU-Speicher geladen.")
    script = Path(__file__).with_name("thorsten_worker.py")
    if not script.is_file():
        raise RuntimeError("Der Thorsten-Langzeitworker ist nicht installiert.")
    log_dir = _studio().data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "thorsten-worker.log"
    with log_path.open("ab") as log:
        os.chmod(log_path, 0o600)
        _MODEL_PROC = subprocess.Popen(
            [config[0], "-u", str(script)],
            cwd=str(Path(config[1]).parent),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=log, text=True, bufsize=1,
        )
    response = _model_message(_MODEL_PROC, 300)
    if response.get("ready") is not True:
        if response.get("error_code") == "CUDA_UNAVAILABLE":
            raise RuntimeError(
                "GPU nicht für CUDA-Ausführung verfügbar. "
                "RunPod-Host/GPU-Zuweisung prüfen; "
                "runpod/gpu_preflight.py --env both ausführen."
            )
        raise RuntimeError("Thorsten-Modell konnte nicht geladen werden.")
    _MODEL_READY = True


def _generate_with_worker(text: str, output: Path, config: tuple[str, str],
                          on_progress=None, speed: float = 1.0) -> None:
    """Synthesize through the already warm persistent worker (or load once)."""
    try:
        _ensure_model_loaded(config, on_progress=on_progress)
        if _MODEL_PROC is None or _MODEL_PROC.stdin is None:
            raise RuntimeError("Thorsten-Worker ist nicht schreibbar.")
        if on_progress is not None:
            on_progress("Sprachsynthese läuft. Die GPU erzeugt die Hörprobe.")
        request_id = uuid.uuid4().hex
        _MODEL_PROC.stdin.write(json.dumps({
            "id": request_id, "text": text, "output": str(output),
            "speed": verified_speed(speed),
        }, ensure_ascii=False) + "\n")
        _MODEL_PROC.stdin.flush()
        response = _model_message(_MODEL_PROC, 600)
        if response.get("id") != request_id or response.get("ok") is not True:
            raise RuntimeError("Thorsten-Worker konnte das Segment nicht erzeugen.")
    except (OSError, ValueError, TimeoutError, RuntimeError, json.JSONDecodeError):
        _close_model()
        raise


def _prewarm_idle_voice() -> None:
    """Proactively load Thorsten as soon as the installer marks it ready.

    Runs on the already existing background production thread, so web startup
    and API responses remain responsive even if model initialization is slow.
    Never preempts a preview or queued book job, and never retries a failed
    preload indefinitely on a paid GPU host.
    """
    global _WARMUP_ATTEMPTED, _WARMUP_STATE
    if (_WARMUP_ATTEMPTED or _STOP.is_set()
            or os.environ.get("AUDIOBOOK_STUDIO_PRELOAD_VOICE", "1") == "0"):
        return
    config = _config()
    if config is None or _model_identity() is None:
        return
    if not _GPU.acquire(blocking=False):
        return
    _WARMUP_ACTIVE.set()
    try:
        if _STOP.is_set():
            return
        _WARMUP_ATTEMPTED = True
        _WARMUP_STATE = "loading"
        _ensure_model_loaded(config)
        if not _STOP.is_set():
            _WARMUP_STATE = "ready"
            logging.getLogger("german-audiobook-studio.engine").info(
                "Thorsten preloaded into GPU memory; persistent worker ready."
            )
    except Exception:
        _WARMUP_STATE = "failed"
        _close_model()
        logging.getLogger("german-audiobook-studio.engine").exception(
            "Thorsten background preload failed; on-demand preview remains available."
        )
    finally:
        _WARMUP_ACTIVE.clear()
        _GPU.release()

def _studio():
    from tts_audiobook_tool.web_studio import app
    return app


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, obj: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            json.dump(obj, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _config() -> tuple[str, str] | None:
    """No user-specified binary flags, shell or remote TTS service allowed."""
    python = os.environ.get("AUDIOBOOK_THORSTEN_PYTHON", "")
    script = os.environ.get("AUDIOBOOK_THORSTEN_SCRIPT", "")
    if not python and not script:
        # A running web studio must discover a newly completed Thorsten
        # install without requiring a webserver restart. Fixed trusted
        # paths only; the model identity still requires the ready marker
        # and checksums before exposing any voice.
        root = _studio().data_dir()
        if not (root / "models/CosyVoice/.studio-ready").is_file():
            return None
        python = str(root / "venvs/thorsten/bin/python")
        script = str(root / "models/CosyVoice/infer_thorsten.py")
    if not (python and script and Path(python).is_file() and Path(script).is_file()):
        return None
    if not (Path(python).is_absolute() and Path(script).is_absolute()):
        return None
    return python, script


def _voice_proof() -> Path:
    return _studio().data_dir() / "voice-samples" / "thorsten-approved.json"


def _model_identity() -> str | None:
    """Bind audible approval to the installed model, version and inference code.

    The installer verifies actual weight bytes with sha256sum before starting
    the web server. This lightweight identity subsequently compares the
    version marker, checksum manifest and installed inference script.
    """
    config = _config()
    if config is None:
        return None
    root = _studio().data_dir() / "models"
    marker = root / "CosyVoice" / ".studio-ready"
    manifest = root / "thorsten-model-sha256.txt"
    script = Path(config[1])
    persistent_worker = Path(__file__).with_name("thorsten_worker.py")
    try:
        version = marker.read_bytes()
        checksums = manifest.read_bytes()
        inference = script.read_bytes()
        worker = persistent_worker.read_bytes()
        if not version or not checksums or not inference or not worker:
            return None
        # The pinned installer marker includes four upstream revision SHAs
        # plus the dependency-lock SHA and runtime versions. Its current
        # canonical UTF-8 length is 289 bytes (including the trailing newline).
        # Keep a conservative bounded read but do not reject a valid install.
        if (len(version) > 512 or len(checksums) > 8192
                or len(inference) > 100_000 or len(worker) > 100_000):
            return None
        return hashlib.sha256(
            version + b"\0" + checksums + b"\0" + inference + b"\0" + worker
        ).hexdigest()
    except OSError:
        return None


def voice_ready() -> bool:
    """An explicit post-listening approval is required, not only a model install."""
    model_identity = _model_identity()
    if not model_identity:
        return False
    try:
        proof = json.loads(_voice_proof().read_text(encoding="utf-8"))
        sample = _voice_proof().parent / "thorsten.wav"
        return (
            proof.get("voice") == VOICE_ID
            and proof.get("model_identity") == model_identity
            and proof.get("sha256") == hashlib.sha256(sample.read_bytes()).hexdigest()
            and _valid_audio(sample) > 0
        )
    except (OSError, ValueError, KeyError, wave.Error):
        return False


def voice_catalogue() -> dict:
    voice = {
        "id": VOICE_ID,
        "name": "Thorsten · CosyVoice3 (Hochdeutsch)",
        "description": "Fertig trainierte deutsche Männerstimme; kein Klonen und keine benutzerdefinierten Sprecheraufnahmen.",
        "status": "ready" if voice_ready() else "under_review",
        "license": "Apache-2.0 (Modell); CC0-1.0 (Trainingsdaten)",
        "source": "https://huggingface.co/Thorsten-Voice/CosyVoice3",
    }
    return {"ready": [voice] if voice_ready() else [], "candidates": [] if voice_ready() else [voice]}


def _valid_audio(path: Path) -> float:
    """Reject empty, silent and truncated PCM WAV files before committing."""
    with wave.open(str(path), "rb") as audio:
        if audio.getnchannels() not in (1, 2) or audio.getframerate() < 16000:
            raise ValueError("Ungeeignetes Audioformat (erwartet PCM-WAV, mindestens 16 kHz).")
        if audio.getsampwidth() not in (2, 3, 4):
            raise ValueError("Audio muss 16-, 24- oder 32-Bit-PCM enthalten.")
        frames = audio.getnframes()
        if frames < audio.getframerate() // 4:
            raise ValueError("Audiosegment ist ungewöhnlich kurz.")
        # Reading only the first second misses files truncated after that point.
        # Verify the complete declared PCM payload before reusing a checkpoint.
        bytes_per_frame = audio.getnchannels() * audio.getsampwidth()
        read_frames = 0
        has_signal = False
        while read_frames < frames:
            payload = audio.readframes(min(65536, frames - read_frames))
            if not payload or len(payload) % bytes_per_frame:
                raise ValueError("Audiosegment ist abgeschnitten oder beschädigt.")
            read_frames += len(payload) // bytes_per_frame
            if not has_signal and any(payload):
                has_signal = True
        if read_frames != frames:
            raise ValueError("Audiosegment ist abgeschnitten oder beschädigt.")
        if not has_signal:
            raise ValueError("Audiosegment enthält keine Sprache.")
        return frames / audio.getframerate()


def _valid_browser_preview(path: Path) -> float:
    """Only expose signed integer PCM-16 WAV as a browser-ready preview.

    The normal audio validator accepts PCM 24/32 for internal workflows.
    A preview must meet the stricter broadly supported 16-bit requirement
    before the API claims the audio can be played in browsers.
    """
    duration = _valid_audio(path)
    with wave.open(str(path), "rb") as audio:
        if audio.getsampwidth() != 2:
            raise ValueError("Hörprobe hat kein browserkompatibles 16-Bit-PCM-Format.")
    return duration


def _audio_quality(path: Path, expected: str) -> dict:
    """Conservative QC flags. ASR discrepancies require human review.

    Uses the same private WAV and original intended spoken prompt; it never
    rewrites the book, changes voice settings or deletes a valid segment.
    """
    warnings = []
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() == 2:
            zero_windows = 0
            max_silence = 0
            clipped = 0
            seen = 0
            while chunk := wav.readframes(wav.getframerate()):
                samples = array("h")
                samples.frombytes(chunk)
                if sys.byteorder != "little":
                    samples.byteswap()
                if not samples:
                    break
                avg = sum(abs(n) for n in samples) / len(samples)
                zero_windows = zero_windows + 1 if avg < 150 else 0
                max_silence = max(max_silence, zero_windows)
                clipped += sum(1 for n in samples if abs(n) >= 32500)
                seen += len(samples)
            if max_silence >= 3:
                warnings.append("Mindestens drei Sekunden fast vollständige Stille.")
            if seen and clipped / seen >= 0.01:
                warnings.append("Mögliche Übersteuerung / Clipping.")
    # CPU ASR is optional, but its absence must never count as a checked
    # segment. The helper reports real runtime state and keeps private
    # transcriptions out of persisted metadata.
    result = asr_quality.evaluate(path, expected)
    result["warnings"] = warnings + result["warnings"]
    return result


def _synthesize(text: str, path: Path, on_progress=None, speed: float = 1.0) -> float:
    config = _config()
    if config is None:
        raise RuntimeError("Thorsten-CosyVoice3 ist nicht installiert.")
    speed = verified_speed(speed)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(path.stem + "." + uuid.uuid4().hex + ".partial.wav")
    try:
        # Official model's infer_thorsten.py CLI; avoid a shell and never
        # download or inject a user-provided voice reference.
        if os.environ.get("AUDIOBOOK_STUDIO_PERSISTENT_TTS", "1") == "1":
            _generate_with_worker(text, temp, config, on_progress=on_progress, speed=speed)
        else:
            # Explicit fallback for debugging only; reloads the full model for
            # every segment and is unsuitable for long production jobs.
            run = subprocess.run(
                [*config, "--text", text, "--output", str(temp), "--speed", str(speed)],
                stdin=subprocess.DEVNULL, capture_output=True,
                timeout=600, check=False, cwd=str(Path(config[1]).parent),
            )
            if run.returncode != 0:
                raise RuntimeError("TTS-Inferenz fehlgeschlagen.")
        if on_progress is not None:
            on_progress("Audiodatei wird geprüft und gespeichert.")
        duration = _valid_audio(temp)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
        return duration
    finally:
        temp.unlink(missing_ok=True)


def _synthesize_with_speed(text: str, path: Path, speed: float, on_progress=None) -> float:
    """Retain existing 1.0× call contracts for historical/test-only workers."""
    speed = verified_speed(speed)
    if speed == 1.0:
        if on_progress is None:
            return _synthesize(text, path)
        return _synthesize(text, path, on_progress=on_progress)
    if on_progress is None:
        return _synthesize(text, path, speed=speed)
    return _synthesize(text, path, on_progress=on_progress, speed=speed)


def project_voice_settings(project_id: str) -> dict:
    """Persistent, private per-book settings; missing file means original 1×."""
    _studio()._project(project_id)
    path = _studio()._project_dir(project_id) / "voice-settings.json"
    if not path.exists():
        return dict(DEFAULT_SETTINGS)
    if path.stat().st_size > 4096:
        raise ValueError("Gespeicherte Sprecherkonfiguration ist beschädigt.")
    try:
        return verified_settings(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise ValueError("Gespeicherte Sprecherkonfiguration ist ungültig.") from exc


def save_project_voice_settings(project_id: str, settings: dict) -> dict:
    """Write under the same DB lock that creates immutable production jobs."""
    settings = verified_settings(settings)
    from .app import _db, _project, _project_dir
    _project(project_id)
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        busy = db.execute(
            "SELECT 1 FROM studio_jobs WHERE project_id=? "
            "AND status IN ('queued','running','paused','failed','exporting')",
            (project_id,),
        ).fetchone()
        if busy:
            raise ValueError("Sprecherkonfiguration ist während eines aktiven oder fortsetzbaren Auftrags gesperrt.")
        _atomic_json(_project_dir(project_id) / "voice-settings.json", settings)
        db.execute(
            "UPDATE studio_projects SET updated_at=? WHERE id=?",
            (_now(), project_id),
        )
    return settings


def make_sample(on_progress=None, duration_seconds: int = 5, *, speed: float = 1.0,
                preview_text: str = "", project_id: str | None = None) -> Path:
    """Audition documented TTS speed with either fixed or custom spoken text."""
    if duration_seconds not in SAMPLE_LENGTHS or type(duration_seconds) is not int:
        raise ValueError("Hörprobenlänge muss 5, 15, 30 oder 60 Sekunden sein.")
    speed = verified_speed(speed)
    preview_text = verified_preview_text(preview_text)
    if project_id is not None:
        _studio()._project(project_id)
    if _model_identity() is None:
        raise ValueError("Thorsten-CosyVoice3 muss zuerst vollständig und geprüft installiert werden.")
    acquired = _GPU.acquire(blocking=False)
    if not acquired and _WARMUP_ACTIVE.is_set():
        # A background preload is initialization, not a competing audiobook
        # job. Let this preview wait for the same worker rather than fail busy.
        if on_progress is not None:
            on_progress("Thorsten wird bereits im Hintergrund in den GPU-Speicher geladen.")
        acquired = _GPU.acquire(timeout=315)
    if not acquired:
        raise ValueError("Die GPU ist durch einen anderen Auftrag belegt.")
    try:
        from .app import _db, data_dir
        with _db() as db:
            busy = db.execute("SELECT 1 FROM studio_jobs WHERE status IN ('running','exporting') LIMIT 1").fetchone()
        if busy:
            raise ValueError("Es läuft bereits eine Hörbuchproduktion.")
        sample = data_dir() / "voice-samples" / "thorsten.wav"
        phrases = (preview_text,) if preview_text else SAMPLE_SENTENCES[:SAMPLE_LENGTHS[duration_seconds]]
        glossary = project_glossary(project_id) if project_id is not None else {}
        if len(phrases) == 1:
            _synthesize_with_speed(
                _narration_prompt(phrases[0], glossary, DE_NORMALIZER_VERSION),
                sample, speed, on_progress=on_progress,
            )
        else:
            # Joining individual sentences keeps each model call short and
            # produces one complete, browser-safe PCM-16 WAV for listening.
            # Intermediate files are private and are removed after assembly.
            import shutil
            fragments_dir = sample.parent / ("preview-" + uuid.uuid4().hex)
            fragments_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
            staged = sample.parent / (".thorsten-" + uuid.uuid4().hex + ".partial.wav")
            try:
                fragments: list[Path] = []
                for index, sentence in enumerate(phrases):
                    if on_progress is not None:
                        on_progress(f"Hörprobe: Satz {index + 1} von {len(phrases)} wird erzeugt.")
                    item = fragments_dir / f"{index:02d}.wav"
                    _synthesize_with_speed(
                        _narration_prompt(sentence, glossary, DE_NORMALIZER_VERSION),
                        item, speed, on_progress=on_progress,
                    )
                    _valid_browser_preview(item)
                    fragments.append(item)
                with wave.open(str(staged), "wb") as joined:
                    expected = None
                    for item in fragments:
                        with wave.open(str(item), "rb") as source:
                            params = (source.getnchannels(), source.getsampwidth(),
                                      source.getframerate())
                            if expected is None:
                                expected = params
                                joined.setnchannels(params[0])
                                joined.setsampwidth(params[1])
                                joined.setframerate(params[2])
                            elif params != expected:
                                raise ValueError("Inkompatible Audioformate in der Hörprobe.")
                            joined.writeframes(source.readframes(source.getnframes()))
                _valid_browser_preview(staged)
                os.chmod(staged, 0o600)
                os.replace(staged, sample)
            finally:
                staged.unlink(missing_ok=True)
                shutil.rmtree(fragments_dir)
        _valid_browser_preview(sample)
        _voice_proof().unlink(missing_ok=True)  # changed audio requires fresh listening
        return sample
    finally:
        _GPU.release()


def _preview_update(task_id: str, *, stage: str, status: str | None = None,
                    error: str = "") -> None:
    """Only fixed, privacy-safe operational messages enter the public activity log."""
    with _PREVIEW_LOCK:
        if _PREVIEW_STATE["id"] != task_id:
            return
        if status is not None:
            _PREVIEW_STATE["status"] = status
        _PREVIEW_STATE["stage"] = stage
        _PREVIEW_STATE["error"] = error
        _PREVIEW_STATE["events"].append({"at": _now(), "message": stage})
        _PREVIEW_STATE["events"] = _PREVIEW_STATE["events"][-40:]


def preview_status() -> dict:
    with _PREVIEW_LOCK:
        return {
            "id": _PREVIEW_STATE["id"], "status": _PREVIEW_STATE["status"],
            "stage": _PREVIEW_STATE["stage"], "error": _PREVIEW_STATE["error"],
            "duration_seconds": _PREVIEW_STATE["duration_seconds"],
            "speed": _PREVIEW_STATE.get("speed", 1.0),
            "custom_text": _PREVIEW_STATE.get("custom_text", False),
            "events": list(_PREVIEW_STATE["events"]),
        }


def _preview_error(exc: Exception) -> str:
    # Never send private prompts, paths or third-party exception traces to
    # browsers. Detailed tracebacks remain in the private server/worker log.
    if isinstance(exc, RuntimeError) and str(exc).startswith("GPU nicht für CUDA-Ausführung"):
        return "Die GPU kann keine CUDA-Operation ausführen. RunPod-GPU-Zuweisung prüfen."
    if isinstance(exc, TimeoutError):
        return "Die Hörprobe hat nicht rechtzeitig geantwortet. Privates Worker-Log prüfen."
    if isinstance(exc, ValueError) and "GPU" in str(exc):
        return "Die GPU ist derzeit durch einen anderen Auftrag belegt."
    return "Die Hörprobe konnte nicht erzeugt werden. Details stehen im privaten Thorsten-Worker-Log."


def _preview_background(task_id: str, duration_seconds: int, speed: float,
                        preview_text: str, project_id: str | None) -> None:
    try:
        _preview_update(task_id, stage="GPU und Sprecherinstallation werden geprüft.")
        generated = make_sample(
            on_progress=lambda message: _preview_update(task_id, stage=message),
            duration_seconds=duration_seconds, speed=speed,
            preview_text=preview_text, project_id=project_id,
        )
        # make_sample checks the audio file, but verify again just before
        # publishing a ready state. Never publish an empty/truncated WAV.
        _valid_browser_preview(generated)
        _preview_update(task_id, status="ready",
                        stage="Hörprobe erstellt und Audiodatei geprüft. Bitte vollständig anhören.")
    except Exception as exc:
        logging.getLogger("german-audiobook-studio.engine").exception(
            "Thorsten preview task failed"
        )
        message = _preview_error(exc)
        _preview_update(task_id, status="failed", stage=message, error=message)


def start_sample(duration_seconds: int = 5, *, speed: float = 1.0,
                 preview_text: str = "", project_id: str | None = None) -> dict:
    if type(duration_seconds) is not int or duration_seconds not in SAMPLE_LENGTHS:
        raise ValueError("Hörprobenlänge muss 5, 15, 30 oder 60 Sekunden sein.")
    speed = verified_speed(speed)
    preview_text = verified_preview_text(preview_text)
    if project_id is not None:
        _studio()._project(project_id)
    if _model_identity() is None:
        raise ValueError("Thorsten-CosyVoice3 muss zuerst vollständig installiert sein.")
    with _PREVIEW_LOCK:
        if _PREVIEW_STATE["status"] == "running":
            raise ValueError("Es wird bereits eine Hörprobe erzeugt.")
        task_id = uuid.uuid4().hex
        _PREVIEW_STATE.update({
            "id": task_id, "status": "running", "stage": "Hörprobe wird vorbereitet.",
            "events": [{"at": _now(), "message": f"Hörprobe mit ungefähr {duration_seconds} Sekunden angefordert."}],
            "error": "", "duration_seconds": duration_seconds,
            "speed": speed, "custom_text": bool(preview_text),
        })
        thread = threading.Thread(
            target=_preview_background,
            args=(task_id, duration_seconds, speed, preview_text, project_id),
            daemon=True, name="studio-preview",
        )
        thread.start()
    return preview_status()


def sample_audio(task_id: str) -> Path:
    with _PREVIEW_LOCK:
        if not re.fullmatch(r"[a-f0-9]{32}", task_id):
            raise ValueError("Ungültige Hörprobe.")
        if _PREVIEW_STATE["id"] != task_id or _PREVIEW_STATE["status"] != "ready":
            raise ValueError("Die Hörprobe ist noch nicht fertig.")
    sample = _voice_proof().parent / "thorsten.wav"
    _valid_browser_preview(sample)
    return sample


def approve_sample() -> None:
    model_identity = _model_identity()
    if model_identity is None:
        raise ValueError("Das installierte Modell hat keinen gültigen Prüfstatus.")
    sample = _voice_proof().parent / "thorsten.wav"
    _valid_audio(sample)
    _atomic_json(_voice_proof(), {
        "voice": VOICE_ID,
        "model_identity": model_identity,
        "sha256": hashlib.sha256(sample.read_bytes()).hexdigest(),
        "approved_at": _now(),
        "model": "Thorsten-Voice/CosyVoice3",
        "license": "Apache-2.0",
        "method": "explicit operator audition",
    })


def project_glossary(project_id: str) -> dict[str, str]:
    path = _studio()._project_dir(project_id) / "engine" / "project.json"
    _studio()._project(project_id)
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        raw = obj.get("word_substitutions", {})
        if not isinstance(raw, dict):
            raise ValueError("Ungültiges Glossar.")
        return {str(k): str(v) for k, v in raw.items()}
    except FileNotFoundError:
        return {}


def save_glossary(project_id: str, entries: dict[str, str]) -> None:
    from tts_audiobook_tool.text_ops.german_speech_normalizer import validate_german_glossary
    _studio()._project(project_id)
    validate_german_glossary(entries)
    with _studio()._db() as db:
        busy = db.execute(
            "SELECT 1 FROM studio_jobs WHERE project_id=? AND status IN ('queued','running','exporting','paused')",
            (project_id,),
        ).fetchone()
    if busy:
        raise ValueError("Das Glossar ist während der Produktion gesperrt.")
    path = _studio()._project_dir(project_id) / "engine" / "project.json"
    if not path.is_file():
        raise ValueError("Bitte zuerst die Kapitelauswahl speichern.")
    obj = json.loads(path.read_text(encoding="utf-8"))
    obj["word_substitutions"] = entries  # Existing Project V5 glossary, no parallel model.
    _atomic_json(path, obj)


def _normalize_de(text: str, glossary: dict[str, str]) -> str:
    """New v3 prompts; only speech text is transformed, never Project V5."""
    from tts_audiobook_tool.text_ops.german_speech_normalizer import (
        VERSION, normalize_german_speech_v3,
    )
    if VERSION != DE_NORMALIZER_VERSION:
        raise RuntimeError("Versionskonflikt in der deutschen Sprachnormalisierung.")
    return normalize_german_speech_v3(text, glossary)


def _normalize_de_v2(text: str, glossary: dict[str, str]) -> str:
    """Frozen v2 for saved jobs; intentionally never upgraded in place."""
    from tts_audiobook_tool.text_ops.german_speech_normalizer import (
        V2_VERSION, normalize_german_speech,
    )
    if V2_VERSION != DE_V2_NORMALIZER_VERSION:
        raise RuntimeError("Versionskonflikt in der deutschen Sprachnormalisierung.")
    return normalize_german_speech(text, glossary)


def _narration_prompt(text: str, glossary: dict[str, str],
                      normalizer_version: str | None) -> str:
    """Single version dispatch for production and neutral voice audition."""
    if normalizer_version is None:
        return _normalize_de_legacy(text, glossary)
    if normalizer_version == DE_V2_NORMALIZER_VERSION:
        return _normalize_de_v2(text, glossary)
    if normalizer_version == DE_NORMALIZER_VERSION:
        return _normalize_de(text, glossary)
    raise ValueError("Unbekannte Normalisierungsversion im gespeicherten Auftrag.")


def _normalize_de_legacy(text: str, glossary: dict[str, str]) -> str:
    """Prompt-only normalization; source and chapter preview remain unchanged."""
    from tts_audiobook_tool.text_ops.prompt_normalizer import PromptNormalizer
    text = PromptNormalizer.apply_prompt_word_substitutions(text, glossary, "de")
    text = text.replace("\u00ad", "").replace("\u00a0", " ")
    for short, long in (("z. B.", "zum Beispiel"), ("d. h.", "das heißt"), ("u. a.", "unter anderem")):
        text = text.replace(short, long)
    # Unambiguous currencies/percentages; ambiguous dates and acronyms are
    # deliberately not guessed. Numeric forms left to the original normalizer.
    text = re.sub(r"\b(\d+)\s*%", r"\1 Prozent", text)
    text = re.sub(r"\b(\d+)\s*€", r"\1 Euro", text)
    return PromptNormalizer.normalize_prompt(text, "de")


def _manifest(project_id: str) -> list[dict]:
    """Read the canonical book.v3 sections/phrase groups, not a second EPUB parser."""
    path = _studio()._project_dir(project_id) / "engine" / "project_text.json"
    try:
        book = json.loads(path.read_text(encoding="utf-8"))
        if book.get("format") != "book.v3":
            raise ValueError("Erwartet wird das vorhandene Project-V5-Buchformat.")
        sections = book["book"]["sections"]
        result = []
        for chapter_index, section in enumerate(sections):
            for group in section["phrase_groups"]:
                raw = "".join(phrase["text"] for phrase in group["phrases"]).strip()
                if raw:
                    result.append({"chapter": chapter_index, "title": section.get("title") or f"Kapitel {chapter_index+1}", "text": raw})
                if len(result) > MAX_SEGMENTS:
                    raise ValueError("Das Buch enthält zu viele Segmente.")
        if not result:
            raise ValueError("Keine sprechbaren Abschnitte vorhanden.")
        return result
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Die gespeicherte Kapitelstruktur ist ungültig.") from exc


def _jobs_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("Ungültiger Auftragsbezeichner.")
    return _studio().data_dir() / "studio-jobs" / job_id


def _job_event(job_id: str, message: str) -> None:
    """Bounded, private per-job activity; never include source text or paths."""
    if not isinstance(message, str) or not 1 <= len(message) <= 200:
        raise ValueError("Ungültige Protokollmeldung.")
    filename = _jobs_dir(job_id) / "activity.json"
    with _ACTIVITY_LOCK:
        try:
            events = json.loads(filename.read_text(encoding="utf-8"))
            if not isinstance(events, list):
                events = []
        except (OSError, ValueError, TypeError):
            events = []
        events.append({"at": _now(), "message": message})
        _atomic_json(filename, events[-60:])


def _job_eta(job_id: str, done: int, total: int) -> tuple[int | None, int]:
    """Use persisted segment measurements; survive paused/restarted jobs.

    Read at most twelve recent checkpoints to calculate inference rate.
    Remaining text length is computed from the immutable job manifest.
    """
    from .progress_estimate import remaining_seconds
    try:
        plan = json.loads((_jobs_dir(job_id) / "plan.json").read_text(encoding="utf-8"))
        segments = plan["segments"]
        if not isinstance(segments, list) or len(segments) != total:
            return None, 0
        samples = []
        for index in range(max(0, done - 20), done):
            file = _jobs_dir(job_id) / "audio" / f"{index:07d}.json"
            try:
                meta = json.loads(file.read_text(encoding="utf-8"))
                chars = len(meta["prompt"])
                duration = float(meta.get("processing_seconds") or meta["inference_seconds"])
                if chars > 0 and 0 < duration < 3600:
                    samples.append((chars, duration))
            except (OSError, ValueError, TypeError, KeyError):
                continue
        remaining_chars = sum(len(item["text"]) for item in segments[done:])
        return remaining_seconds(samples, remaining_chars), min(len(samples), 12)
    except (OSError, ValueError, TypeError, KeyError):
        return None, 0


def job_activity(project_id: str, job_id: str) -> dict:
    """Authenticated and project-isolated progress; never return raw worker logs."""
    job = public_job(project_id, job_id)
    filename = _jobs_dir(job_id) / "activity.json"
    with _ACTIVITY_LOCK:
        try:
            events = json.loads(filename.read_text(encoding="utf-8"))
            if not isinstance(events, list):
                events = []
        except (OSError, ValueError, TypeError):
            events = []
    total = max(0, job["total"])
    done = max(0, min(job["done"], total))
    # M4B muxing has a separate progress stage; do not attach a fabricated
    # export ETA to the finished speech synthesis progress.
    eta, sample_count = _job_eta(job_id, done, total) if done < total else (0, 0)
    if job["status"] not in ("running", "generated", "completed"):
        eta = None
    now = datetime.now(timezone.utc)
    try:
        started_at = datetime.fromisoformat(job["created_at"])
        elapsed = max(0, int((now - started_at).total_seconds()))
    except (ValueError, TypeError):
        elapsed = 0
    return {
        "id": job_id, "status": job["status"],
        "done": done, "total": total,
        "percent": round(100 * done / total) if total else 0,
        "elapsed_seconds": elapsed,
        "remaining_seconds": eta,
        "eta_sample_count": sample_count,
        "calculated_at": now.isoformat(),
        "events": events[-60:],
    }


def init():
    """Recover interrupted jobs on restart; one worker per persistent data root."""
    global _THREAD, _LOCK_FILE, _WARMUP_ATTEMPTED, _WARMUP_STATE
    if _THREAD and _THREAD.is_alive():
        return
    # Acquire the process lock *before* touching running job statuses: an
    # accidental second web process must not reset work owned by the first.
    import fcntl
    lockfile = _studio().data_dir() / ".gpu-worker.lock"
    lockfile.touch(mode=0o600, exist_ok=True)
    handle = lockfile.open("r+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError("Studio unterstützt nur einen Webserver-Prozess je Datenverzeichnis.")
    _LOCK_FILE = handle
    with _studio()._db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS studio_jobs (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL, voice_id TEXT NOT NULL,
          status TEXT NOT NULL, done INTEGER NOT NULL, total INTEGER NOT NULL,
          chapter TEXT NOT NULL, duration REAL NOT NULL, error TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        db.execute("UPDATE studio_jobs SET status='queued', updated_at=? WHERE status='running'", (_now(),))
        db.execute("UPDATE studio_jobs SET status='generated', updated_at=? WHERE status='exporting'", (_now(),))
    # Ignore interrupted atomically staged audio and export files; they will
    # be regenerated instead of being considered successful outputs.
    for staged in (_studio().data_dir() / "studio-jobs").glob("*/audio/*.partial.wav"):
        staged.unlink(missing_ok=True)
    for staged in (_studio().data_dir() / "studio-jobs").glob("*/audiobook.m4b.partial"):
        staged.unlink(missing_ok=True)
    _STOP.clear()
    _WARMUP_ATTEMPTED = False
    _WARMUP_STATE = "waiting"
    _WARMUP_ACTIVE.clear()
    _WAKE.set()
    _THREAD = threading.Thread(target=_loop, name="studio-gpu-worker", daemon=True)
    _THREAD.start()


def stop():
    global _THREAD, _LOCK_FILE
    _STOP.set()
    _WAKE.set()
    # A SIGTERM while torch is running must not mark resumable segments as
    # permanently failed. They will be checked and retried after restart.
    with _studio()._db() as db:
        db.execute("UPDATE studio_jobs SET status='queued', updated_at=? WHERE status='running'", (_now(),))
    _close_model()
    if _THREAD:
        _THREAD.join(timeout=2)
    if _THREAD and _THREAD.is_alive():
        # A running model call may still be waiting for its timeout. Retain
        # the process lock until process exit; never allow a second worker.
        return
    _THREAD = None
    if _LOCK_FILE:
        _LOCK_FILE.close()
    _LOCK_FILE = None


def _job(job_id: str) -> dict:
    with _studio()._db() as db:
        row = db.execute("SELECT * FROM studio_jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise ValueError("Auftrag wurde nicht gefunden.")
    return dict(row)


def public_job(project_id: str, job_id: str) -> dict:
    _studio()._project(project_id)
    job = _job(job_id)
    if job["project_id"] != project_id:
        raise ValueError("Auftrag wurde nicht gefunden.")
    return job


def list_jobs(project_id: str) -> list[dict]:
    _studio()._project(project_id)
    with _studio()._db() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM studio_jobs WHERE project_id=? ORDER BY created_at DESC", (project_id,)
        ).fetchall()]


def create_job(project_id: str) -> dict:
    project = _studio()._project(project_id)
    if project["status"] not in ("Vorbereitet", "Fertig"):
        raise ValueError("Bitte zuerst die Kapitelauswahl speichern.")
    if not voice_ready():
        raise ValueError("Keine freigegebene deutsche Stimme: zuerst eine echte Hörprobe prüfen.")
    # Persist the canonical Project-V5 text once as a versioned production
    # snapshot. B01 source offsets are joined, never re-extracted from EPUB.
    glossary = project_glossary(project_id)
    voice_settings = project_voice_settings(project_id)
    catalogue_path = _studio()._project_dir(project_id) / "chapters.json"
    source_indices = None
    if catalogue_path.is_file():
        catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
        excluded = set(json.loads(project["excluded_json"]))
        source_indices = [
            i for i in range(project["selected_start"], len(catalogue["chapters"]))
            if i not in excluded
        ]
    plan = narration_plan.build_narration_plan(
        project_id, _studio()._project_dir(project_id), source_indices,
        glossary, VOICE_ID, _model_identity() or "",
        DE_NORMALIZER_VERSION, _narration_prompt, created_at=_now(),
        voice_settings={"speed": voice_settings["speed"]},
    )
    segments = plan["segments"]
    job_id = uuid.uuid4().hex
    # Serialize the active-job check with job creation across API requests.
    # Without BEGIN IMMEDIATE, simultaneous requests can create duplicate jobs.
    try:
        with _studio()._db() as db:
            db.execute("BEGIN IMMEDIATE")
            # Project deletion can race with manifest preparation. Re-check
            # the canonical project row *inside* this same writer transaction:
            # otherwise a deleted project could gain an orphan audio job.
            current = db.execute(
                "SELECT updated_at FROM studio_projects WHERE id=? AND status IN ('Vorbereitet','Fertig')",
                (project_id,),
            ).fetchone()
            if current is None or current["updated_at"] != project["updated_at"]:
                raise ValueError("Das Projekt wurde zwischenzeitlich gelöscht oder verändert.")
            if db.execute(
                "SELECT 1 FROM studio_jobs WHERE project_id=? AND status IN ('queued','running','paused','exporting')",
                (project_id,),
            ).fetchone():
                raise ValueError("Für dieses Projekt ist bereits ein Auftrag aktiv.")
            _atomic_json(_jobs_dir(job_id) / "plan.json", plan)
            db.execute("INSERT INTO studio_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                job_id, project_id, VOICE_ID, "queued", 0, len(segments), "", 0.0, "", _now(), _now()
            ))
            db.execute("UPDATE studio_projects SET status='Warteschlange',updated_at=? WHERE id=?",
                       (_now(), project_id))
    except Exception:
        # An uncommitted plan must never become a visible orphan job.
        (_jobs_dir(job_id) / "plan.json").unlink(missing_ok=True)
        raise
    _job_event(job_id, "Hörbuchauftrag wurde in die Warteschlange gestellt.")
    _WAKE.set()
    return public_job(project_id, job_id)


def change_job(project_id: str, job_id: str, action: str) -> dict:
    job = public_job(project_id, job_id)
    targets = {
        "pause": ({"queued", "running"}, "paused"),
        "resume": ({"paused", "failed"}, "queued"),
        "cancel": ({"queued", "running", "paused", "failed"}, "cancelled"),
    }
    if action not in targets or job["status"] not in targets[action][0]:
        raise ValueError("Dieser Zustandswechsel ist nicht zulässig.")
    with _studio()._db() as db:
        updated = db.execute(
            "UPDATE studio_jobs SET status=?,updated_at=? WHERE id=? AND status=?",
            (targets[action][1], _now(), job_id, job["status"]),
        ).rowcount
        if updated != 1:
            raise ValueError("Der Auftragsstatus hat sich inzwischen geändert.")
        if action == "cancel":
            # A cancelled job must not strand the book in "Warteschlange".
            # The old in-flight segment can finish, but it cannot change the
            # cancelled job's state or mark the project as completed.
            db.execute(
                "UPDATE studio_projects SET status='Vorbereitet',updated_at=? WHERE id=?",
                (_now(), project_id),
            )
    action_labels = {
        "pause": "Hörbuchproduktion wurde pausiert.",
        "resume": "Hörbuchproduktion wird fortgesetzt.",
        "cancel": "Hörbuchproduktion wurde abgebrochen.",
    }
    _job_event(job_id, action_labels[action])
    _WAKE.set()
    return public_job(project_id, job_id)


def _loop():
    while not _STOP.is_set():
        _WAKE.wait(timeout=2)
        _WAKE.clear()
        if _STOP.is_set():
            break
        with _studio()._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM studio_jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row is not None:
                db.execute("UPDATE studio_jobs SET status='running',updated_at=? WHERE id=? AND status='queued'", (_now(), row["id"]))
        if row is None:
            _prewarm_idle_voice()
            continue
        try:
            _job_event(row["id"], "GPU-Auftrag gestartet; Sprecher wird vorbereitet.")
            _run(row["id"])
        except Exception as exc:
            with _studio()._db() as db:
                db.execute("UPDATE studio_jobs SET status='failed',error=?,updated_at=? WHERE id=? AND status='running'",
                           (str(exc)[:400],_now(),row["id"]))
            _job_event(row["id"], "Vertonung fehlgeschlagen. Einzelheiten im privaten Server-Log.")
            logging.getLogger("german-audiobook-studio.engine").exception(
                "Audiobook production failed, job=%s", row["id"]
            )


def _audio_digest(path: Path) -> str:
    """Stream WAV integrity digest; do not load multi-hour audio into RAM."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _segment_fingerprint(segment: dict, plan: dict) -> str:
    if plan.get("version") == narration_plan.PLAN_VERSION:
        return narration_plan.segment_fingerprint(segment, plan)
    version = plan.get("normalizer_version")
    if version not in (None, DE_V2_NORMALIZER_VERSION, DE_NORMALIZER_VERSION):
        raise ValueError("Unbekannte Normalisierungsversion im gespeicherten Auftrag.")
    # Legacy plans must retain byte-for-byte their original cache hash.
    source = (
        segment["text"] + "\0" + json.dumps(plan["glossary"], sort_keys=True)
        + "\0" + VOICE_ID + "\0" + plan.get("model_identity", "")
    )
    if version:
        source += "\0" + version
    return hashlib.sha256(source.encode()).hexdigest()


def _run(job_id: str):
    # Export and manual previews may briefly own the GPU lock. Waiting must
    # not permanently fail a queued job that has not started inference yet.
    _GPU.acquire()
    try:
        job = _job(job_id)
        plan = json.loads((_jobs_dir(job_id) / "plan.json").read_text(encoding="utf-8"))
        is_narration_plan = narration_plan.validate_narration_plan(plan)
        segments = plan["segments"]
        if is_narration_plan:
            if plan["project_id"] != job["project_id"] or len(segments) != job["total"]:
                raise ValueError("NarrationPlan und Produktionsauftrag stimmen nicht überein.")
            narration_plan.assert_book_unchanged(
                _studio()._project_dir(job["project_id"]), plan
            )
            if project_glossary(job["project_id"]) != plan["glossary"]:
                raise ValueError("Das Projektglossar wurde seit Auftragsbeginn verändert.")
        if plan.get("model_identity") and plan["model_identity"] != _model_identity():
            raise ValueError("Die Modellgewichte oder der Inferenzcode haben sich seit Auftragsbeginn geändert.")
        chapter = ""
        duration = 0.0
        done = 0
        for i, segment in enumerate(segments):
            if _STOP.is_set() or _job(job_id)["status"] != "running":
                return
            path = _jobs_dir(job_id) / "audio" / f"{i:07d}.wav"
            normalizer_version = plan.get("normalizer_version")
            fingerprint = _segment_fingerprint(segment, plan)
            meta = path.with_suffix(".json")
            seconds = 0.0
            if path.exists() and meta.exists():
                try:
                    sidecar = json.loads(meta.read_text(encoding="utf-8"))
                    valid = sidecar.get("fingerprint") == fingerprint
                    if is_narration_plan:
                        valid = valid and (
                            sidecar.get("unit_id") == segment["unit_id"]
                            and sidecar.get("prompt") == segment["speech_text"]
                            and sidecar.get("plan_schema") == narration_plan.SCHEMA
                            and sidecar.get("audio_sha256") == _audio_digest(path)
                        )
                    if valid:
                        seconds = _valid_audio(path)
                except (ValueError, OSError, wave.Error):
                    seconds = 0.0
            if not seconds:
                if i == 0 or i % 10 == 0:
                    _job_event(job_id, f"Sprachsynthese: Segment {i + 1} von {len(segments)} läuft.")
                path.unlink(missing_ok=True)
                meta.unlink(missing_ok=True)
                # B03 prompts are already frozen in the validated plan: do not
                # normalize a second time. Historical v2/legacy stays unchanged.
                prompt = (segment["speech_text"] if is_narration_plan else
                          _narration_prompt(segment["text"], plan["glossary"], normalizer_version))
                if not prompt:
                    raise ValueError("Leeres Sprachsegment nach Textaufbereitung.")
                infer_started = time.perf_counter()
                voice_speed = verified_speed(plan.get("voice_settings", {"speed": 1.0})["speed"])
                for attempt in range(3):
                    try:
                        seconds = _synthesize_with_speed(prompt, path, voice_speed)
                        break
                    except (RuntimeError, ValueError, subprocess.TimeoutExpired, wave.Error):
                        if attempt == 2:
                            raise
                        time.sleep(1)
                inference_seconds = round(time.perf_counter() - infer_started, 3)
                quality = _audio_quality(path, prompt)
                processing_seconds = round(time.perf_counter() - infer_started, 3)
                sidecar = {"fingerprint": fingerprint, "text": segment["text"],
                           "prompt": prompt, "duration": seconds,
                           "inference_seconds": inference_seconds,
                           "processing_seconds": processing_seconds, "quality": quality}
                if normalizer_version == DE_NORMALIZER_VERSION:
                    sidecar["normalizer_version"] = normalizer_version
                if is_narration_plan:
                    sidecar.update({
                        "plan_schema": narration_plan.SCHEMA,
                        "unit_id": segment["unit_id"],
                        "chapter_id": segment["chapter_id"],
                        "config_fingerprint": plan["config_fingerprint"],
                        "audio_sha256": _audio_digest(path),
                    })
                _atomic_json(meta, sidecar)
            # Pause/cancel/shutdown may have arrived during a long GPU call.
            # Preserve the valid segment, but never overwrite the new status.
            if _STOP.is_set() or _job(job_id)["status"] != "running":
                return
            duration += seconds
            done += 1
            chapter = segment["title"]
            with _studio()._db() as db:
                db.execute("UPDATE studio_jobs SET done=?,duration=?,chapter=?,updated_at=? WHERE id=?",
                           (done,duration,chapter,_now(),job_id))
            if done == 1 or done % 10 == 0 or done == len(segments):
                _job_event(job_id, f"{done} von {len(segments)} Audiosegmenten fertig.")
        with _studio()._db() as db:
            completed = db.execute(
                "UPDATE studio_jobs SET status='generated',done=?,duration=?,updated_at=? WHERE id=? AND status='running'",
                (done, duration, _now(), job_id),
            ).rowcount
            if completed == 1:
                db.execute("UPDATE studio_projects SET status='Audio bereit',updated_at=? WHERE id=?",
                           (_now(), job["project_id"]))
        if completed == 1:
            _job_event(job_id, "Alle Audiosegmente wurden erstellt. M4B-Export kann gestartet werden.")
    finally:
        _GPU.release()


def audio_path(project_id: str, job_id: str, index: int) -> Path:
    job = public_job(project_id, job_id)
    if not 0 <= index < job["total"]:
        raise ValueError("Segment außerhalb des Auftrags.")
    path = _jobs_dir(job_id) / "audio" / f"{index:07d}.wav"
    if not path.is_file():
        raise ValueError("Das Segment wurde noch nicht erzeugt.")
    return path


def quality_report(project_id: str, job_id: str) -> dict:
    job = public_job(project_id, job_id)
    issues = []
    checked = 0
    for index in range(job["total"]):
        meta = _jobs_dir(job_id) / "audio" / f"{index:07d}.json"
        if not meta.is_file():
            # Missing segment metadata must not claim that the (possibly
            # unavailable) ASR step has taken place.
            issues.append({"index": index, "status": "not_checked",
                           "reason": "Kein Segmentprüfbericht vorhanden.",
                           "warnings": [], "asr_similarity": None})
            continue
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            quality = info.get("quality", {})
            if not isinstance(quality, dict):
                raise ValueError("Invalid quality metadata")
            status = quality.get("status", "not_checked")
            if status in ("passed", "review"):
                checked += 1
            if status != "passed" or quality.get("warnings"):
                issues.append({
                    "index": index,
                    "status": status,
                    "reason": quality.get("reason") or (
                        "ASR-Prüfung nicht durchgeführt." if status == "not_checked" else ""),
                    "warnings": quality.get("warnings", []),
                    "asr_similarity": quality.get("asr_similarity"),
                    "asr_wer": quality.get("asr_wer"),
                    "asr_cer": quality.get("asr_cer"),
                    "asr_number_mismatch": quality.get("asr_number_mismatch", False),
                })
        except (OSError, ValueError, TypeError):
            issues.append({"index": index, "status": "not_checked",
                           "reason": "Segmentprüfbericht beschädigt.",
                           "warnings": [], "asr_similarity": None})
    return {"checked": checked, "unchecked": job["total"] - checked,
            "total": job["total"], "issues": issues}


def retry_segment(project_id: str, job_id: str, index: int) -> dict:
    # A retry invalidates one cached segment. It must never remove files
    # during export or while another API request owns the GPU state.
    if not _GPU.acquire(blocking=False):
        raise ValueError("Audioverarbeitung läuft; bitte den Vorgang erneut versuchen.")
    try:
        job = public_job(project_id, job_id)
        if job["status"] not in ("generated", "completed"):
            raise ValueError("Einzelne Segmente können erst nach der Generierung erneut erzeugt werden.")
        if not 0 <= index < job["total"]:
            raise ValueError("Ungültiger Segmentindex.")
        if not voice_ready():
            raise ValueError("Die freigegebene deutsche Stimme ist nicht verfügbar.")
        path = _jobs_dir(job_id) / "audio" / f"{index:07d}.wav"
        # The worker cannot see a queued job before the cache invalidation
        # finishes: BEGIN IMMEDIATE keeps the queue transition atomic.
        with _studio()._db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT status FROM studio_jobs WHERE id=?", (job_id,)).fetchone()
            if current is None or current["status"] not in ("generated", "completed"):
                raise ValueError("Der Auftragsstatus hat sich inzwischen geändert.")
            path.unlink(missing_ok=True)
            path.with_suffix(".json").unlink(missing_ok=True)
            # Retain the previous completed M4B until the replacement is
            # exported atomically. It remains inaccessible while queued.
            db.execute(
                "UPDATE studio_jobs SET status='queued',done=0,duration=0,chapter='',error='',updated_at=? WHERE id=?",
                (_now(), job_id),
            )
            db.execute("UPDATE studio_projects SET status='Warteschlange',updated_at=? WHERE id=?",
                       (_now(), project_id))
    finally:
        _GPU.release()
    _WAKE.set()
    return public_job(project_id, job_id)



def _export_chapter_key(segment: dict) -> tuple[str, object]:
    """Identity from book.v3 section index, never the human-facing title.

    The title fallback is exclusively for historical, manually staged plans
    that predate the canonical chapter field. New plans always carry it.
    """
    index = segment.get("chapter")
    if index is None:
        index = segment.get("chapter_index")
    if index is not None:
        return ("section", index)
    return ("legacy_title", segment.get("title") or "")


def export_job(project_id: str, job_id: str) -> Path:
    job = public_job(project_id, job_id)
    if job["status"] not in ("generated", "completed"):
        raise ValueError("Alle Audiosegmente müssen fertig sein.")
    if not _GPU.acquire(blocking=False):
        raise ValueError("Die GPU ist belegt.")
    try:
        # A retry or cancellation may have changed the state while this
        # export request waited for the lock.
        job = public_job(project_id, job_id)
        if job["status"] not in ("generated", "completed"):
            raise ValueError("Alle Audiosegmente müssen fertig sein.")
        from tts_audiobook_tool.sound.audio_writers import AacWriter, Chapter
        import numpy as np
        import soundfile as sf
        plan = json.loads((_jobs_dir(job_id) / "plan.json").read_text(encoding="utf-8"))
        is_narration_plan = narration_plan.validate_narration_plan(plan)
        if is_narration_plan:
            if plan["project_id"] != project_id or len(plan["segments"]) != job["total"]:
                raise ValueError("NarrationPlan und Exportauftrag stimmen nicht überein.")
            narration_plan.assert_book_unchanged(_studio()._project_dir(project_id), plan)
            if project_glossary(project_id) != plan["glossary"]:
                raise ValueError("Das Projektglossar wurde seit Auftragsbeginn verändert.")
        # Even a completed export cannot bypass a damaged/new-version plan.
        if job["status"] == "completed":
            existing = _jobs_dir(job_id) / "audiobook.m4b"
            if existing.is_file():
                return existing
        path = _jobs_dir(job_id) / "audiobook.m4b"
        start = 0.0
        chapters = []
        chapter_title = ""
        chapter_start = 0.0
        chapter_key = None
        sample_rate = 0
        writer = None
        with _studio()._db() as db:
            db.execute("UPDATE studio_jobs SET status='exporting',updated_at=? WHERE id=?", (_now(),job_id))
        _job_event(job_id, "M4B-Export gestartet. Audiosegmente werden zusammengeführt.")
        try:
            for i, segment in enumerate(plan["segments"]):
                if is_narration_plan:
                    meta = _jobs_dir(job_id) / "audio" / f"{i:07d}.json"
                    try:
                        sidecar = json.loads(meta.read_text(encoding="utf-8"))
                    except (OSError, ValueError, TypeError) as exc:
                        raise ValueError("NarrationPlan: Segment-Checkpoint fehlt oder ist beschädigt.") from exc
                    if (sidecar.get("fingerprint") != _segment_fingerprint(segment, plan)
                            or sidecar.get("unit_id") != segment["unit_id"]
                            or sidecar.get("prompt") != segment["speech_text"]
                            or sidecar.get("plan_schema") != narration_plan.SCHEMA
                            or sidecar.get("audio_sha256") != _audio_digest(
                                audio_path(project_id, job_id, i)
                            )):
                        raise ValueError("NarrationPlan: Audiosegment passt nicht zum Produktionsplan.")
                audio, sr = sf.read(str(audio_path(project_id,job_id,i)), dtype="float32", always_2d=True)
                if writer is None:
                    sample_rate = sr
                    writer = AacWriter(str(path), sample_rate, audio.shape[1], "96k", {"title": _studio()._project(project_id)["title"]})
                if sr != sample_rate or audio.shape[1] != writer.channels:
                    raise ValueError("Unterschiedliche Audioformate: bitte Segmente vereinheitlichen.")
                current_key = _export_chapter_key(segment)
                if current_key != chapter_key:
                    if chapter_key is not None:
                        chapters.append(Chapter(chapter_start, start, chapter_title))
                    chapter_title = (segment.get("title") or "").strip() or f"Kapitel {len(chapters) + 1}"
                    chapter_start = start
                    chapter_key = current_key
                writer.write(np.asarray(audio, dtype=np.float32))
                start += len(audio) / sr
                if (i + 1) % 25 == 0 or i + 1 == len(plan["segments"]):
                    _job_event(job_id, f"M4B-Export: {i + 1} von {len(plan['segments'])} Segmenten verarbeitet.")
            if chapter_key is not None:
                chapters.append(Chapter(chapter_start, start, chapter_title))
            if writer is None:
                raise ValueError("Keine Audiodaten für den Export.")
            writer.set_chapters(chapters)
            writer.close()
        except Exception:
            if writer is not None:
                writer.abort()
            with _studio()._db() as db:
                db.execute("UPDATE studio_jobs SET status='generated',error='Export fehlgeschlagen',updated_at=? WHERE id=?", (_now(),job_id))
            _job_event(job_id, "M4B-Export fehlgeschlagen. Privates Server-Log prüfen.")
            raise
        with _studio()._db() as db:
            db.execute("UPDATE studio_jobs SET status='completed',error='',updated_at=? WHERE id=?", (_now(),job_id))
            db.execute("UPDATE studio_projects SET status='Fertig',updated_at=? WHERE id=?", (_now(),project_id))
        _job_event(job_id, "M4B-Hörbuch wurde erfolgreich erstellt.")
        return path
    finally:
        _GPU.release()
