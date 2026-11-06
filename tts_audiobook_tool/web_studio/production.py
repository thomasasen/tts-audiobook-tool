"""Persisted, single-GPU German audiobook production pipeline.

This module uses the already-imported Project V5 book.v3 phrase groups. It
does not parse EPUB a second time or mutate the book text. Production is
disabled unless an operator has installed and auditioned a curated voice.
"""
from __future__ import annotations

import hashlib
import difflib
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

VOICE_ID = "thorsten-cosyvoice3"
SAMPLE_TEXT = "Guten Tag. Dieses Fachbuch erklärt, wie gute Entscheidungen im Vertrieb entstehen."
MAX_SEGMENTS = 100_000
MAX_GLOSSARY = 500
_WAKE = threading.Event()
_STOP = threading.Event()
_GPU = threading.Lock()
_THREAD: threading.Thread | None = None
_LOCK_FILE = None
_MODEL_PROC = None
_ASR_MODEL = None


def _close_model() -> None:
    global _MODEL_PROC
    proc = _MODEL_PROC
    _MODEL_PROC = None
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


def _generate_with_worker(text: str, output: Path, config: tuple[str, str]) -> None:
    """The only active worker lives inside the exclusive GPU critical section."""
    global _MODEL_PROC
    try:
        if _MODEL_PROC is None or _MODEL_PROC.poll() is not None:
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
                raise RuntimeError("Thorsten-Modell konnte nicht geladen werden.")
        if _MODEL_PROC.stdin is None:
            raise RuntimeError("Thorsten-Worker ist nicht schreibbar.")
        request_id = uuid.uuid4().hex
        _MODEL_PROC.stdin.write(json.dumps({
            "id": request_id, "text": text, "output": str(output)
        }, ensure_ascii=False) + "\n")
        _MODEL_PROC.stdin.flush()
        response = _model_message(_MODEL_PROC, 600)
        if response.get("id") != request_id or response.get("ok") is not True:
            raise RuntimeError("Thorsten-Worker konnte das Segment nicht erzeugen.")
    except (OSError, ValueError, TimeoutError, RuntimeError, json.JSONDecodeError):
        _close_model()
        raise


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
    try:
        version = marker.read_bytes()
        checksums = manifest.read_bytes()
        inference = script.read_bytes()
        if not version or not checksums or not inference:
            return None
        if len(version) > 256 or len(checksums) > 8192 or len(inference) > 100_000:
            return None
        return hashlib.sha256(version + b"\\0" + checksums + b"\\0" + inference).hexdigest()
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
    result = {"status": "not_checked", "warnings": warnings, "asr_similarity": None}
    name = os.environ.get("AUDIOBOOK_STUDIO_ASR_MODEL", "")
    if not name:
        result["reason"] = "ASR nicht aktiviert."
        return result
    if name not in ("tiny", "base", "small", "medium"):
        result["reason"] = "Ungültige Whisper-Modellkonfiguration."
        return result
    global _ASR_MODEL
    try:
        if _ASR_MODEL is None:
            from faster_whisper import WhisperModel
            _ASR_MODEL = WhisperModel(name, device="cpu", compute_type="int8")
        transcript_segments, _ = _ASR_MODEL.transcribe(str(path), language="de", beam_size=3)
        transcript = " ".join(s.text.strip() for s in transcript_segments).strip()
        expected_words = re.findall(r"\w+", expected.casefold(), flags=re.UNICODE)
        actual_words = re.findall(r"\w+", transcript.casefold(), flags=re.UNICODE)
        similarity = difflib.SequenceMatcher(a=expected_words, b=actual_words, autojunk=False).ratio()
        result["status"] = "review" if similarity < 0.75 else "passed"
        result["asr_similarity"] = round(similarity, 3)
        result["transcript"] = transcript[:12000]
        if similarity < 0.75:
            warnings.append("ASR und erwarteter Text weichen deutlich voneinander ab.")
        # This is a heuristic token similarity, not an accuracy/word-error-rate claim.
        return result
    except Exception:
        result["status"] = "unavailable"
        result["reason"] = "Whisper-Prüfung nicht verfügbar. Manuelle Hörkontrolle nötig."
        return result


def _synthesize(text: str, path: Path) -> float:
    config = _config()
    if config is None:
        raise RuntimeError("Thorsten-CosyVoice3 ist nicht installiert.")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(path.stem + "." + uuid.uuid4().hex + ".partial.wav")
    try:
        # Official model's infer_thorsten.py CLI; avoid a shell and never
        # download or inject a user-provided voice reference.
        if os.environ.get("AUDIOBOOK_STUDIO_PERSISTENT_TTS", "1") == "1":
            _generate_with_worker(text, temp, config)
        else:
            # Explicit fallback for debugging only; reloads the full model for
            # every segment and is unsuitable for long production jobs.
            run = subprocess.run(
                [*config, "--text", text, "--output", str(temp)],
                stdin=subprocess.DEVNULL, capture_output=True,
                timeout=600, check=False, cwd=str(Path(config[1]).parent),
            )
            if run.returncode != 0:
                raise RuntimeError("TTS-Inferenz fehlgeschlagen.")
        duration = _valid_audio(temp)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
        return duration
    finally:
        temp.unlink(missing_ok=True)


def make_sample() -> Path:
    if _model_identity() is None:
        raise ValueError("Thorsten-CosyVoice3 muss zuerst vollständig und geprüft installiert werden.")
    if not _GPU.acquire(blocking=False):
        raise ValueError("Die GPU ist durch einen anderen Auftrag belegt.")
    try:
        from .app import _db, data_dir
        with _db() as db:
            busy = db.execute("SELECT 1 FROM studio_jobs WHERE status IN ('running','exporting') LIMIT 1").fetchone()
        if busy:
            raise ValueError("Es läuft bereits eine Hörbuchproduktion.")
        sample = data_dir() / "voice-samples" / "thorsten.wav"
        _synthesize(SAMPLE_TEXT, sample)
        _voice_proof().unlink(missing_ok=True)  # changed audio requires fresh listening
        return sample
    finally:
        _GPU.release()


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
    _studio()._project(project_id)
    if len(entries) > MAX_GLOSSARY or any(
        not isinstance(k, str) or not isinstance(v, str)
        or not 1 <= len(k) <= 100 or not 1 <= len(v) <= 180
        or "\n" in k or "\n" in v or "<" in v or ">" in v
        for k, v in entries.items()
    ):
        raise ValueError("Das Glossar darf höchstens 500 gültige Begriffe enthalten.")
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


def init():
    """Recover interrupted jobs on restart; one worker per persistent data root."""
    global _THREAD, _LOCK_FILE
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
    segments = _manifest(project_id)
    glossary = project_glossary(project_id)
    job_id = uuid.uuid4().hex
    plan = {
        "version": 2, "project_id": project_id, "voice": VOICE_ID,
        "model_identity": _model_identity() or "",
        "segments": segments, "glossary": glossary, "created_at": _now(),
    }
    # Serialize the active-job check with job creation across API requests.
    # Without BEGIN IMMEDIATE, simultaneous requests can create duplicate jobs.
    try:
        with _studio()._db() as db:
            db.execute("BEGIN IMMEDIATE")
            # Project deletion can race with manifest preparation. Re-check
            # the canonical project row *inside* this same writer transaction:
            # otherwise a deleted project could gain an orphan audio job.
            if db.execute(
                "SELECT 1 FROM studio_projects WHERE id=? AND status IN ('Vorbereitet','Fertig')",
                (project_id,),
            ).fetchone() is None:
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
            continue
        try:
            _run(row["id"])
        except Exception as exc:
            with _studio()._db() as db:
                db.execute("UPDATE studio_jobs SET status='failed',error=?,updated_at=? WHERE id=? AND status='running'",
                           (str(exc)[:400],_now(),row["id"]))


def _run(job_id: str):
    # Export and manual previews may briefly own the GPU lock. Waiting must
    # not permanently fail a queued job that has not started inference yet.
    _GPU.acquire()
    try:
        job = _job(job_id)
        plan = json.loads((_jobs_dir(job_id) / "plan.json").read_text(encoding="utf-8"))
        segments = plan["segments"]
        if plan.get("model_identity") and plan["model_identity"] != _model_identity():
            raise ValueError("Die Modellgewichte oder der Inferenzcode haben sich seit Auftragsbeginn geändert.")
        chapter = ""
        duration = 0.0
        done = 0
        for i, segment in enumerate(segments):
            if _STOP.is_set() or _job(job_id)["status"] != "running":
                return
            path = _jobs_dir(job_id) / "audio" / f"{i:07d}.wav"
            fingerprint = hashlib.sha256(
                (segment["text"] + "\0" + json.dumps(plan["glossary"], sort_keys=True)
                 + "\0" + VOICE_ID + "\0" + plan.get("model_identity", "")).encode()
            ).hexdigest()
            meta = path.with_suffix(".json")
            seconds = 0.0
            if path.exists() and meta.exists():
                try:
                    if json.loads(meta.read_text(encoding="utf-8")).get("fingerprint") == fingerprint:
                        seconds = _valid_audio(path)
                except (ValueError, OSError, wave.Error):
                    seconds = 0.0
            if not seconds:
                path.unlink(missing_ok=True)
                meta.unlink(missing_ok=True)
                prompt = _normalize_de(segment["text"], plan["glossary"])
                if not prompt:
                    raise ValueError("Leeres Sprachsegment nach Textaufbereitung.")
                infer_started = time.perf_counter()
                for attempt in range(3):
                    try:
                        seconds = _synthesize(prompt, path)
                        break
                    except (RuntimeError, ValueError, subprocess.TimeoutExpired, wave.Error):
                        if attempt == 2:
                            raise
                        time.sleep(1)
                inference_seconds = round(time.perf_counter() - infer_started, 3)
                _atomic_json(meta, {"fingerprint": fingerprint, "text": segment["text"],
                                    "prompt": prompt, "duration": seconds,
                                    "inference_seconds": inference_seconds,
                                    "quality": _audio_quality(path, prompt)})
            # Pause/cancel/shutdown may have arrived during a long GPU call.
            # Preserve the valid segment, but never overwrite the new status.
            if _STOP.is_set() or _job(job_id)["status"] != "running":
                return
            duration += seconds
            done += 1
            if segment["title"] != chapter or done % 5 == 0 or done == len(segments):
                chapter = segment["title"]
                with _studio()._db() as db:
                    db.execute("UPDATE studio_jobs SET done=?,duration=?,chapter=?,updated_at=? WHERE id=?",
                               (done,duration,chapter,_now(),job_id))
        with _studio()._db() as db:
            completed = db.execute(
                "UPDATE studio_jobs SET status='generated',done=?,duration=?,updated_at=? WHERE id=? AND status='running'",
                (done, duration, _now(), job_id),
            ).rowcount
            if completed == 1:
                db.execute("UPDATE studio_projects SET status='Audio bereit',updated_at=? WHERE id=?",
                           (_now(), job["project_id"]))
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
            continue
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            quality = info.get("quality", {})
            if quality.get("status") in ("passed", "review"):
                checked += 1
            if quality.get("status") in ("review", "unavailable") or quality.get("warnings"):
                issues.append({
                    "index": index,
                    "status": quality.get("status", "not_checked"),
                    "warnings": quality.get("warnings", []),
                    "asr_similarity": quality.get("asr_similarity"),
                })
        except (OSError, ValueError, TypeError):
            issues.append({"index": index, "status": "unavailable",
                           "warnings": ["Segmentprüfbericht beschädigt."]})
    return {"checked": checked, "total": job["total"], "issues": issues}


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
        if job["status"] == "completed":
            existing = _jobs_dir(job_id) / "audiobook.m4b"
            if existing.is_file():
                return existing
        from tts_audiobook_tool.sound.audio_writers import AacWriter, Chapter
        import numpy as np
        import soundfile as sf
        plan = json.loads((_jobs_dir(job_id) / "plan.json").read_text(encoding="utf-8"))
        path = _jobs_dir(job_id) / "audiobook.m4b"
        start = 0.0
        chapters = []
        chapter_title = ""
        chapter_start = 0.0
        sample_rate = 0
        writer = None
        with _studio()._db() as db:
            db.execute("UPDATE studio_jobs SET status='exporting',updated_at=? WHERE id=?", (_now(),job_id))
        try:
            for i, segment in enumerate(plan["segments"]):
                audio, sr = sf.read(str(audio_path(project_id,job_id,i)), dtype="float32", always_2d=True)
                if writer is None:
                    sample_rate = sr
                    writer = AacWriter(str(path), sample_rate, audio.shape[1], "96k", {"title": _studio()._project(project_id)["title"]})
                if sr != sample_rate or audio.shape[1] != writer.channels:
                    raise ValueError("Unterschiedliche Audioformate: bitte Segmente vereinheitlichen.")
                if segment["title"] != chapter_title:
                    if chapter_title:
                        chapters.append(Chapter(chapter_start, start, chapter_title))
                    chapter_title, chapter_start = segment["title"], start
                writer.write(np.asarray(audio, dtype=np.float32))
                start += len(audio) / sr
            if chapter_title:
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
            raise
        with _studio()._db() as db:
            db.execute("UPDATE studio_jobs SET status='completed',error='',updated_at=? WHERE id=?", (_now(),job_id))
            db.execute("UPDATE studio_projects SET status='Fertig',updated_at=? WHERE id=?", (_now(),project_id))
        return path
    finally:
        _GPU.release()
