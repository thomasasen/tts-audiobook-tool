"""Authenticated German Audiobook Studio: project library and EPUB preparation.

Launch with:
    AUDIOBOOK_STUDIO_TOKEN=<random 32+ characters> \
    AUDIOBOOK_STUDIO_DATA_DIR=/workspace/audiobook-data \
    uvicorn tts_audiobook_tool.web_studio.app:app --host 0.0.0.0 --port 7860

Dependencies are declared in web/requirements.txt. No GPU is needed to
import and inspect an EPUB. Never publish the private data directory.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import uuid
import wave
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from fastapi import Body, Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from . import production, asr_quality

MAX_UPLOAD_BYTES = 60 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 300 * 1024 * 1024
MAX_ZIP_ENTRIES = 5000
MAX_CHAPTERS = 5000
FIRST_TEXT_CHARS = 1500
TOC = re.compile(r"^\s*(?:inhalts(?:verzeichnis|übersicht|uebersicht)|table\s+of\s+contents|contents|toc)\s*$", re.I)
PREFACE = re.compile(r"^\s*(?:vorwort|geleitwort|foreword|preface)(?:\b|\s|:)", re.I)
UUID_RE = re.compile(r"^[0-9a-f]{32}$")
# Changes on every web process restart, exposed only to authenticated users.
INSTANCE_ID = uuid.uuid4().hex


def data_dir() -> Path:
    return Path(os.environ.get("AUDIOBOOK_STUDIO_DATA_DIR", "/workspace/audiobook-data")).expanduser().resolve()


def _require_token() -> str:
    token = os.environ.get("AUDIOBOOK_STUDIO_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("AUDIOBOOK_STUDIO_TOKEN muss mindestens 32 Zeichen lang sein.")
    return token


def require_auth(authorization: Annotated[str | None, Header()] = None) -> None:
    token = _require_token()
    supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    if not hmac.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(status_code=401, detail="Zugriff verweigert. Bitte gültigen Zugangsschlüssel eingeben.")


def _init_storage() -> None:
    root = data_dir()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (root / "studio-projects").mkdir(mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    os.chmod(root / "studio-projects", 0o700)
    with _db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS studio_projects (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
            filename TEXT NOT NULL, chapter_count INTEGER NOT NULL,
            selected_start INTEGER NOT NULL, excluded_json TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
    os.chmod(root / "studio.sqlite3", 0o600)


@contextmanager
def _db():
    path = data_dir() / "studio.sqlite3"
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    finally:
        db.close()


def _project_dir(project_id: str) -> Path:
    if not UUID_RE.fullmatch(project_id):
        raise HTTPException(404, "Projekt wurde nicht gefunden.")
    return data_dir() / "studio-projects" / project_id


def _project(project_id: str) -> dict:
    _project_dir(project_id)
    with _db() as db:
        row = db.execute("SELECT * FROM studio_projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Projekt wurde nicht gefunden.")
    return dict(row)


def _validate_epub(path: Path) -> None:
    try:
        if not zipfile.is_zipfile(path):
            raise ValueError("Die Datei ist kein gültiges EPUB-/ZIP-Archiv.")
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise ValueError("Das EPUB enthält zu viele Archiveinträge.")
            total = 0
            seen = set()
            for item in entries:
                name = item.filename
                parts = PurePosixPath(name).parts
                if not name or name.startswith("/") or "\\" in name or ".." in parts:
                    raise ValueError("Das EPUB enthält einen unzulässigen Archivpfad.")
                if name in seen:
                    raise ValueError("Das EPUB enthält doppelte Archivpfade.")
                seen.add(name)
                if item.flag_bits & 0x1:
                    raise ValueError("Verschlüsselte EPUB-Dateien werden nicht unterstützt.")
                if (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Symbolische Links sind im EPUB nicht zulässig.")
                total += item.file_size
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("Das entpackte EPUB überschreitet das Größenlimit.")
                if item.file_size > 10 * 1024 * 1024 and item.file_size > max(1, item.compress_size) * 300:
                    raise ValueError("Verdächtiges Kompressionsverhältnis im EPUB.")
            if "mimetype" not in seen or archive.read("mimetype").strip() != b"application/epub+zip":
                raise ValueError("Die EPUB-Kennzeichnung fehlt oder ist ungültig.")
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise ValueError("Das EPUB kann nicht sicher gelesen werden.") from exc


def _extract(
        path: Path,
        include_skipped_hrefs: set[str] | None = None,
        speaker_overrides: dict[str, dict[str, str]] | None = None,
):
    # Use the application's canonical parser, including spine and navigation
    # logic. Never parse user-provided HTML in the web layer.
    from tts_audiobook_tool.text_ops.epub_extractor import EpubExtractor
    result = EpubExtractor.extract_import_chapters(
        str(path),
        include_skipped_hrefs=include_skipped_hrefs,
        speaker_overrides=speaker_overrides,
    )
    if not result.chapters:
        raise ValueError("Das EPUB enthält keine auslesbaren Kapitel.")
    if len(result.chapters) > MAX_CHAPTERS:
        raise ValueError("Das EPUB enthält zu viele Kapitel.")
    # The audit is read-only and supplements, never replaces, the canonical parser.
    from tts_audiobook_tool.text_ops.epub_content_audit import audit_epub_content
    result.content_audit = audit_epub_content(
        str(path),
        result.chapters,
        result.significant_warnings or result.warnings,
        skipped_sources=result.skipped_sources,
        speaker_overrides=speaker_overrides,
        included_source_hrefs=include_skipped_hrefs,
    )
    result.speaker_overrides = speaker_overrides or {}
    return result


def _catalogue(result) -> dict:
    chapters = [
        {
            "index": i,
            "title": (chapter.title or f"Abschnitt {i + 1}")[:250],
            "preview": chapter.text[:FIRST_TEXT_CHARS],
            "characters": len(chapter.text),
            "is_toc": bool(TOC.fullmatch((chapter.title or "").strip())),
        }
        for i, chapter in enumerate(result.chapters)
    ]
    candidates = [c["index"] for c in chapters if not c["is_toc"]]
    if not candidates:
        raise ValueError("Nur Inhaltsverzeichnisse gefunden. Kein Vorlesetext vorhanden.")
    prefaces = [i for i in candidates if PREFACE.match(chapters[i]["title"])]
    start = prefaces[0] if prefaces else candidates[0]
    return {
        "title": (result.book_title or "Unbenanntes Buch")[:250],
        "chapters": chapters,
        "suggested_start": start,
        "warnings": (result.significant_warnings or result.warnings)[:50],
        "content_audit": getattr(result, "content_audit", {
            "version": 2, "status": "unavailable", "summary": {},
            "resources": [], "skipped": [], "source_mapping": [],
            "chapter_coverage": {}, "restorable_source_hrefs": [],
            "review_elements": [],
            "warnings": ["EPUB-Content-Audit nicht verfügbar."],
            "review_notes": ["Importinhalt manuell kontrollieren."],
        }),
        "included_source_hrefs": list(getattr(result, "included_source_hrefs", [])),
        "speaker_overrides": [
            {"source_href": href, "element_key": key, "speaker_text": value, "approved": True}
            for href, values in sorted(getattr(result, "speaker_overrides", {}).items())
            for key, value in sorted(values.items())
            if isinstance(value, str) and value.strip()
        ],
    }


def _public_project(row: dict, catalogue: dict | None = None) -> dict:
    item = {
        "id": row["id"], "title": row["title"], "status": row["status"],
        "filename": row["filename"], "chapter_count": row["chapter_count"],
        "selected_start": row["selected_start"],
        "excluded_indices": json.loads(row["excluded_json"]),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
    if catalogue is not None:
        item.update(catalogue)
    return item


class ChapterSelection(BaseModel):
    start_index: int = Field(ge=0)
    excluded_indices: list[int] = Field(default_factory=list, max_length=MAX_CHAPTERS)


class SourceOverridePayload(BaseModel):
    include_hrefs: list[str] = Field(default_factory=list, max_length=MAX_CHAPTERS)


class SpeakerOverridePayload(BaseModel):
    source_href: str = Field(min_length=1, max_length=1024)
    element_key: str = Field(min_length=1, max_length=64)
    # Empty text removes an existing override; non-empty text is explicit human approval.
    speaker_text: str = Field(max_length=1000)


def _speaker_override_map(catalogue: dict) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for item in catalogue.get("speaker_overrides", []):
        if not isinstance(item, dict):
            continue
        href = item.get("source_href")
        key = item.get("element_key")
        text = item.get("speaker_text")
        if isinstance(href, str) and isinstance(key, str) and isinstance(text, str) and text.strip():
            result.setdefault(href, {})[key] = text.strip()
    return result


def _write_catalogue(directory: Path, catalogue: dict) -> None:
    destination = directory / "chapters.json"
    temporary = directory / f"chapters.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8") as target:
            target.write(json.dumps(catalogue, ensure_ascii=False))
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _refresh_draft_catalogue(project_id: str, catalogue: dict) -> dict:
    directory = _project_dir(project_id)
    _write_catalogue(directory, catalogue)
    now = datetime.now(timezone.utc).isoformat()
    with _db() as db:
        db.execute(
            """UPDATE studio_projects
               SET title=?, chapter_count=?, selected_start=?, excluded_json=?, status=?, updated_at=?
               WHERE id=?""",
            (
                catalogue["title"], len(catalogue["chapters"]),
                catalogue["suggested_start"], "[]", "Entwurf", now, project_id,
            ),
        )
    return _public_project(_project(project_id), catalogue)


app = FastAPI(
    title="German Audiobook Studio", version="0.1.0",
    description="Geschützte Projektvorbereitung für deutsche EPUB-Hörbücher.",
    docs_url=None, redoc_url=None, openapi_url=None,
)


@app.middleware("http")
async def secure_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self' blob:; img-src 'self' data:; media-src 'self' blob:; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    return response


def _init_engine_logging() -> None:
    """Initialize the logger expected by the original Project V5 persistence.

    The upstream TUI initializes L during App startup; FastAPI does not run
    that startup. Keep web logs in the protected data root, never /tmp.
    """
    from tts_audiobook_tool.l import L

    if getattr(L, "logger", None) is not None:
        return
    directory = data_dir() / "logs"
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    log_path = directory / "studio-engine.log"
    log_path.touch(mode=0o600, exist_ok=True)
    os.chmod(log_path, 0o600)
    logger = logging.getLogger("german-audiobook-studio.engine")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logger.addHandler(logging.FileHandler(log_path, encoding="utf-8"))
    L.logger = logger


@app.on_event("startup")
def on_startup():
    _require_token()
    _init_storage()
    _init_engine_logging()
    production.init()


@app.on_event("shutdown")
def on_shutdown():
    production.stop()


@app.get("/api/health")
def health():
    # Public health reveals neither data nor secrets.
    return {"status": "ok", "service": "german-audiobook-studio"}


@app.get("/api/session", dependencies=[Depends(require_auth)])
def session():
    return {"authenticated": True, "language": "de", "instance_id": INSTANCE_ID}


@app.get("/api/projects", dependencies=[Depends(require_auth)])
def projects():
    with _db() as db:
        rows = db.execute("SELECT * FROM studio_projects ORDER BY updated_at DESC").fetchall()
    return {"projects": [_public_project(dict(row)) for row in rows]}


@app.post("/api/projects", dependencies=[Depends(require_auth)], status_code=201)
async def create_project(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".epub"):
        raise HTTPException(400, "Bitte eine EPUB-Datei auswählen.")
    project_id = uuid.uuid4().hex
    directory = _project_dir(project_id)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    path = directory / "source.epub"
    total = 0
    try:
        # Chunked upload: reject files that exceed the maximum even if the
        # client omits Content-Length.
        with path.open("xb") as target:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise ValueError("Die EPUB-Datei ist größer als 60 MB.")
                target.write(chunk)
        os.chmod(path, 0o600)
        _validate_epub(path)
        result = _extract(path)
        catalogue = _catalogue(result)
        (directory / "chapters.json").write_text(
            json.dumps(catalogue, ensure_ascii=False), encoding="utf-8"
        )
        now = datetime.now(timezone.utc).isoformat()
        with _db() as db:
            db.execute(
                """INSERT INTO studio_projects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id, catalogue["title"], "Entwurf",
                    Path(file.filename).name[:255], len(catalogue["chapters"]),
                    catalogue["suggested_start"], "[]", now, now,
                ),
            )
        return _public_project(_project(project_id), catalogue)
    except ValueError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        await file.close()


@app.get("/api/projects/{project_id}", dependencies=[Depends(require_auth)])
def get_project(project_id: str):
    row = _project(project_id)
    path = _project_dir(project_id) / "chapters.json"
    try:
        catalogue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(500, "Kapitelinformationen können nicht geladen werden.") from exc
    # Existing projects created by an earlier Studio build remain readable.
    catalogue.setdefault("included_source_hrefs", [])
    catalogue.setdefault("speaker_overrides", [])
    audit = catalogue.get("content_audit")
    if isinstance(audit, dict):
        audit.setdefault("source_mapping", [])
        audit.setdefault("chapter_coverage", {})
        audit.setdefault("restorable_source_hrefs", [])
        audit.setdefault("review_elements", [])
    return _public_project(row, catalogue)


@app.put("/api/projects/{project_id}/source-overrides", dependencies=[Depends(require_auth)])
def update_source_overrides(project_id: str, payload: SourceOverridePayload):
    row = _project(project_id)
    if row["status"] != "Entwurf":
        raise HTTPException(409, "Quellenauslassungen können nur vor dem Speichern der Kapitelauswahl geändert werden.")
    directory = _project_dir(project_id)
    try:
        current = json.loads((directory / "chapters.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(500, "Kapitelinformationen können nicht geladen werden.") from exc
    allowed = set(current.get("content_audit", {}).get("restorable_source_hrefs", []))
    requested = set(payload.include_hrefs)
    if len(requested) != len(payload.include_hrefs):
        raise HTTPException(422, "Quellressourcen dürfen nicht doppelt angegeben werden.")
    if any(not isinstance(href, str) or href not in allowed for href in requested):
        raise HTTPException(422, "Mindestens eine Quellressource ist nicht als korrigierbare Auslassung freigegeben.")
    overrides = _speaker_override_map(current)
    try:
        result = _extract(directory / "source.epub", requested, overrides)
        catalogue = _catalogue(result)
        return _refresh_draft_catalogue(project_id, catalogue)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.put("/api/projects/{project_id}/speaker-overrides", dependencies=[Depends(require_auth)])
def update_speaker_override(project_id: str, payload: SpeakerOverridePayload):
    row = _project(project_id)
    if row["status"] != "Entwurf":
        raise HTTPException(409, "Sprecherfassungen können nur vor dem Speichern der Kapitelauswahl geändert werden.")
    directory = _project_dir(project_id)
    try:
        current = json.loads((directory / "chapters.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(500, "Kapitelinformationen können nicht geladen werden.") from exc

    element = next((
        item for item in current.get("content_audit", {}).get("review_elements", [])
        if item.get("source_href") == payload.source_href
        and item.get("element_key") == payload.element_key
        and item.get("can_edit")
    ), None)
    if element is None:
        raise HTTPException(422, "Das Inhaltselement gehört nicht zu einer aktuell übernommenen EPUB-Quellressource.")

    overrides = _speaker_override_map(current)
    clean_text = payload.speaker_text.strip()
    if clean_text:
        overrides.setdefault(payload.source_href, {})[payload.element_key] = clean_text
    else:
        values = overrides.get(payload.source_href, {})
        values.pop(payload.element_key, None)
        if not values:
            overrides.pop(payload.source_href, None)

    include_hrefs = set(current.get("included_source_hrefs", []))
    try:
        result = _extract(directory / "source.epub", include_hrefs, overrides)
        catalogue = _catalogue(result)
        return _refresh_draft_catalogue(project_id, catalogue)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.put("/api/projects/{project_id}/chapters", dependencies=[Depends(require_auth)])
def select_chapters(project_id: str, selection: ChapterSelection):
    row = _project(project_id)
    if row["status"] not in {"Entwurf", "Vorbereitet"}:
        raise HTTPException(409, "Das Projekt kann in seinem aktuellen Zustand nicht bearbeitet werden.")
    directory = _project_dir(project_id)
    catalogue = json.loads((directory / "chapters.json").read_text(encoding="utf-8"))
    chapters = catalogue["chapters"]
    if selection.start_index >= len(chapters) or chapters[selection.start_index]["is_toc"]:
        raise HTTPException(422, "Ungültiger Vorlesebeginn: Ein Inhaltsverzeichnis ist nicht zulässig.")
    excluded = set(selection.excluded_indices)
    if any(i < 0 or i >= len(chapters) for i in excluded):
        raise HTTPException(422, "Ungültige Kapitelauswahl.")
    excluded.update(i for i, chapter in enumerate(chapters) if chapter["is_toc"])
    included = [i for i in range(selection.start_index, len(chapters)) if i not in excluded]
    if not included or included[0] != selection.start_index:
        raise HTTPException(422, "Mindestens das Startkapitel muss enthalten sein.")

    # Import and persist with the existing project schema, not a new book
    # representation. No TTS execution is triggered by this operation.
    from tts_audiobook_tool.project import Project
    from tts_audiobook_tool.project_support.project_text_io_util import ProjectTextIOUtil
    from tts_audiobook_tool.text_ops.epub_extractor import EpubExtractor

    try:
        speaker_overrides = _speaker_override_map(catalogue)
        book = EpubExtractor.import_epub(
            str(directory / "source.epub"),
            max_words=Project().max_words,
            segmentation_strategy=Project().segmentation_strategy,
            language_code="de",
            include_skipped_hrefs=set(catalogue.get("included_source_hrefs", [])),
            speaker_overrides=speaker_overrides,
        )
        if len(book.chapters) != len(chapters):
            raise ValueError("Die EPUB-Kapitel haben sich seit dem Import verändert.")
        bounds = [0] + book.section_start_indices + [len(book.phrase_groups)]
        selected_groups = []
        selected_titles = []
        selected_markers = []
        selected_raw = []
        for i in included:
            if selected_groups:
                selected_markers.append(len(selected_groups))
            selected_groups.extend(book.phrase_groups[bounds[i]:bounds[i + 1]])
            selected_titles.append(book.chapters[i].title)
            selected_raw.append(book.chapters[i].text)
        if not selected_groups:
            raise ValueError("Die Auswahl enthält keinen sprechbaren Text.")

        engine_dir = directory / "engine"
        engine_dir.mkdir(mode=0o700, exist_ok=True)
        project = Project(dir_path=str(engine_dir), language_code="de")
        error = ProjectTextIOUtil.set_phrase_groups_chapters_and_save(
            project, phrase_groups=selected_groups,
            section_start_indices=selected_markers,
            strategy=project.segmentation_strategy,
            max_words=project.max_words,
            language_code="de",
            raw_text="\n\n".join(selected_raw),
            title=book.book_title,
            section_titles=selected_titles,
        )
        if error:
            raise ValueError("Projektspeicherung fehlgeschlagen: " + error)
        error = EpubExtractor.copy_epub_to_project(str(directory / "source.epub"), str(engine_dir))
        if error:
            raise ValueError("EPUB-Kopie fehlgeschlagen: " + error)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    now = datetime.now(timezone.utc).isoformat()
    with _db() as db:
        db.execute(
            """UPDATE studio_projects SET selected_start=?, excluded_json=?, status=?, updated_at=? WHERE id=?""",
            (selection.start_index, json.dumps(sorted(excluded)), "Vorbereitet", now, project_id),
        )
    return get_project(project_id)


@app.get("/api/voices", dependencies=[Depends(require_auth)])
def voices():
    return production.voice_catalogue()


@app.post("/api/voices/preview", dependencies=[Depends(require_auth)])
def preview_voice():
    try:
        path = production.make_sample()
        return FileResponse(path, media_type="audio/wav", filename="thorsten-probe.wav")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        logging.getLogger("german-audiobook-studio").exception("Thorsten preview failed")
        if str(exc).startswith("GPU nicht für CUDA-Ausführung verfügbar."):
            raise HTTPException(
                503,
                "Die GPU ist für CUDA nicht betriebsbereit. Der RunPod-Host muss "
                "geprüft oder gewechselt werden. Diagnose: "
                "python3 /workspace/tts-audiobook-tool/runpod/gpu_preflight.py --env both",
            ) from exc
        raise HTTPException(
            503,
            "Die Thorsten-Hörprobe konnte nicht erzeugt werden. "
            "Details stehen im RunPod-Webserver-Log und in "
            "/workspace/audiobook-data/logs/thorsten-worker.log.",
        ) from exc
    except (OSError, TimeoutError) as exc:
        # Inference/loading failures are operational incidents, not JSON parse
        # failures. Preserve the traceback privately and return safe JSON.
        logging.getLogger("german-audiobook-studio").exception("Thorsten preview failed")
        raise HTTPException(
            503,
            "Die Thorsten-Hörprobe konnte nicht erzeugt werden. "
            "Details stehen im RunPod-Webserver-Log und in "
            "/workspace/audiobook-data/logs/thorsten-worker.log.",
        ) from exc


class PreviewOptions(BaseModel):
    duration_seconds: Literal[5, 15, 30, 60] = 5
    # Preserve JSON types so True / "1.0" cannot silently coerce to 1.0.
    # The single authoritative validator is verified_speed in production.
    speed: Any = 1.0
    preview_text: str = Field(default="", max_length=600)
    project_id: str | None = None


class VoiceSettingsOptions(BaseModel):
    speed: Any = 1.0
    preview_text: str = Field(default="", max_length=600)


@app.get("/api/projects/{project_id}/voice-settings", dependencies=[Depends(require_auth)])
def get_voice_settings(project_id: str):
    try:
        return production.project_voice_settings(project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.put("/api/projects/{project_id}/voice-settings", dependencies=[Depends(require_auth)])
def put_voice_settings(project_id: str, options: VoiceSettingsOptions):
    try:
        return production.save_project_voice_settings(project_id, options.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/voices/preview/start", dependencies=[Depends(require_auth)], status_code=202)
def start_preview(options: PreviewOptions = Body(default=PreviewOptions())):
    """Fast response: model load and inference continue in a single background worker."""
    try:
        return production.start_sample(
            duration_seconds=options.duration_seconds,
            speed=options.speed, preview_text=options.preview_text,
            project_id=options.project_id,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/voices/preview/status", dependencies=[Depends(require_auth)])
def preview_status():
    return production.preview_status()


@app.get("/api/voices/preview/audio/{preview_id}", dependencies=[Depends(require_auth)])
def preview_audio(preview_id: str):
    try:
        path = production.sample_audio(preview_id)
        return FileResponse(
            path, media_type="audio/wav", filename="thorsten-probe.wav",
            headers={"Cache-Control": "no-store"},
        )
    except (ValueError, OSError, wave.Error) as exc:
        raise HTTPException(404, "Keine vollständig erzeugte Hörprobe verfügbar.") from exc


@app.post("/api/voices/approve", dependencies=[Depends(require_auth)])
def approve_voice():
    # Called only after the operator has auditioned the generated sample.
    try:
        production.approve_sample()
        return {"approved": production.voice_ready()}
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "Zuerst eine gültige Hörprobe erzeugen und anhören.") from exc


# Only fixed non-secret state codes are accepted from the private RunPod
# bootstrap. No log contents, token or arbitrary file path reaches the client.
VOICE_INSTALL_MESSAGES = {
    "initializing": "Der RunPod-Start bereitet Thorsten vor. Bitte Installationsfortschritt im Container-Log prüfen.",
    "checking-cache": "Die vorhandene Thorsten-Installation wird auf Unversehrtheit geprüft.",
    "downloading": "Thorsten-Modellgewichte werden heruntergeladen und geprüft. Die Hörprobe ist noch nicht bereit.",
    "installing": "Die Thorsten-Laufzeit und Modellgewichte werden installiert und geprüft.",
    "insufficient-space": "Thorsten wurde nicht installiert: Für den ersten Download müssen mindestens 25 GiB auf dem Network Volume frei sein.",
    "cuda-unavailable": "Thorsten wurde nicht installiert: Der echte CUDA-Tensortest des GPU-Hosts ist fehlgeschlagen. GPU-Zuweisung und RunPod-Host prüfen.",
    "download-failed": "Der Thorsten-Modelldownload ist fehlgeschlagen. Das private Thorsten-Prefetch- und Autostart-Log auf RunPod prüfen.",
    "installation-failed": "Die Thorsten-Installation ist fehlgeschlagen. Das private Autostart-Log enthält den fehlgeschlagenen Installationsschritt.",
    "voice-cuda-unavailable": "Thorsten wurde installiert, aber CUDA funktioniert in seiner Python-Laufzeit nicht. GPU-Host und Thorsten-CUDA-Test prüfen.",
    "bootstrap-failed": "Der RunPod-Start wurde vor Abschluss der Sprecherinstallation abgebrochen. Das private Autostart-Log prüfen.",
    "skipped": "Die Sprecherinstallation wurde ausdrücklich übersprungen (RUNPOD_TTS_SKIP_VOICE_INSTALL=1).",
}
 
@app.get("/api/capabilities", dependencies=[Depends(require_auth)])
def capabilities():
    # Installation and explicit approval are independent states. The Web
    # Studio starts before the asynchronous Thorsten installation completes;
    # compute both on EVERY request rather than caching startup state.
    from .production import _model_identity
    installed = _model_identity() is not None
    approved = installed and production.voice_ready()
    warmup = production.voice_warmup_state() if installed else "unavailable"
    try:
        install_state = (data_dir() / "studio-voice-install-state").read_text(
            encoding="ascii"
        ).strip()
    except (OSError, UnicodeError):
        install_state = "unknown"
    if install_state not in VOICE_INSTALL_MESSAGES:
        install_state = "unknown"
    if installed:
        install_state = "installed"
    if approved:
        reason = "Thorsten-CosyVoice3 ist installiert und nach Hörprobe freigegeben."
    elif installed:
        reason = (
            "Thorsten-CosyVoice3 ist installiert. Bitte eine echte Hörprobe "
            "erzeugen, vollständig anhören und anschließend freigeben."
        )
        if warmup == "loading":
            reason += " Die Stimme wird bereits im Hintergrund in den GPU-Speicher geladen."
        elif warmup == "ready":
            reason += " Die Stimme ist bereits im GPU-Speicher vorgeladen."
        elif warmup == "failed":
            reason += " Das automatische Vorladen ist fehlgeschlagen; die Hörprobe kann den Worker erneut starten."
    else:
        reason = VOICE_INSTALL_MESSAGES.get(
            install_state,
            "Thorsten-CosyVoice3 ist noch nicht vollständig installiert oder konnte nicht verifiziert werden. Den RunPod-Autostart-Status und "
            "die privaten Installationslogs prüfen. Die Hörprobe ist gesperrt.",
        )
    return {
        "voice_install_state": install_state,
        "epub_import": True,
        "chapter_selection": True,
        "tts_generation": approved,
        "audio_export": True,
        "voice_installed": installed,
        "voice_approved": approved,
        "voice_preview_available": installed,
        "voice_model_loaded": installed and production._model_loaded(),
        "voice_warmup_state": warmup,
        "asr": asr_quality.capabilities(),
        "reason": reason,
    }


class GlossaryPayload(BaseModel):
    entries: dict[str, str] = Field(default_factory=dict)


@app.get("/api/projects/{project_id}/glossary", dependencies=[Depends(require_auth)])
def get_glossary(project_id: str):
    return {"entries": production.project_glossary(project_id)}


@app.put("/api/projects/{project_id}/glossary", dependencies=[Depends(require_auth)])
def put_glossary(project_id: str, payload: GlossaryPayload):
    try:
        production.save_glossary(project_id, payload.entries)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return get_glossary(project_id)


@app.get("/api/projects/{project_id}/jobs", dependencies=[Depends(require_auth)])
def list_project_jobs(project_id: str):
    return {"jobs": production.list_jobs(project_id)}


@app.post("/api/projects/{project_id}/jobs", dependencies=[Depends(require_auth)], status_code=201)
def start_job(project_id: str):
    try:
        return production.create_job(project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/projects/{project_id}/jobs/{job_id}", dependencies=[Depends(require_auth)])
def get_job(project_id: str, job_id: str):
    try:
        return production.public_job(project_id, job_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/projects/{project_id}/jobs/{job_id}/activity", dependencies=[Depends(require_auth)])
def get_job_activity(project_id: str, job_id: str):
    try:
        return production.job_activity(project_id, job_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class JobAction(BaseModel):
    action: str


@app.post("/api/projects/{project_id}/jobs/{job_id}/control", dependencies=[Depends(require_auth)])
def control_job(project_id: str, job_id: str, payload: JobAction):
    try:
        return production.change_job(project_id, job_id, payload.action)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/projects/{project_id}/jobs/{job_id}/segments/{index}", dependencies=[Depends(require_auth)])
def download_segment(project_id: str, job_id: str, index: int):
    try:
        path = production.audio_path(project_id, job_id, index)
        return FileResponse(path, media_type="audio/wav", filename=f"segment-{index}.wav")
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/projects/{project_id}/jobs/{job_id}/chapters", dependencies=[Depends(require_auth)])
def list_chapter_files(project_id: str, job_id: str):
    """Only fully checkpointed, encoded chapters appear; works mid-production."""
    try:
        return {"chapters": production.completed_chapter_files(project_id, job_id)}
    except (ValueError, OSError) as exc:
        raise HTTPException(404, "Keine Kapiteldateien für diesen Auftrag verfügbar.") from exc


@app.get("/api/projects/{project_id}/jobs/{job_id}/chapters/{chapter_index}/audio",
         dependencies=[Depends(require_auth)])
def play_chapter(project_id: str, job_id: str, chapter_index: int):
    """Authorized AAC file for the authenticated fetch-to-Blob chapter player."""
    try:
        path = production.completed_chapter_path(project_id, job_id, chapter_index)
        return FileResponse(
            path, media_type="audio/mp4",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
            content_disposition_type="inline",
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(404, "Das Kapitel ist noch nicht als vollständige Audiodatei verfügbar.") from exc


@app.get("/api/projects/{project_id}/jobs/{job_id}/chapters/{chapter_index}/download",
         dependencies=[Depends(require_auth)])
def download_chapter(project_id: str, job_id: str, chapter_index: int):
    """Only numeric, application-derived names. Never expose private file paths."""
    try:
        path = production.completed_chapter_path(project_id, job_id, chapter_index)
        return FileResponse(
            path, media_type="audio/mp4",
            filename=f"Kapitel-{chapter_index + 1:03d}.m4a",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(404, "Das Kapitel ist noch nicht als vollständige Audiodatei verfügbar.") from exc


@app.get("/api/projects/{project_id}/jobs/{job_id}/quality", dependencies=[Depends(require_auth)])
def get_quality(project_id: str, job_id: str):
    try:
        return production.quality_report(project_id, job_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/projects/{project_id}/jobs/{job_id}/segments/{index}/retry", dependencies=[Depends(require_auth)])
def retry_segment(project_id: str, job_id: str, index: int):
    try:
        return production.retry_segment(project_id, job_id, index)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/projects/{project_id}/jobs/{job_id}/export", dependencies=[Depends(require_auth)])
def create_export(project_id: str, job_id: str):
    try:
        path = production.export_job(project_id, job_id)
        return {"ready": path.is_file(), "filename": "audiobook.m4b"}
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/projects/{project_id}/jobs/{job_id}/download", dependencies=[Depends(require_auth)])
def download_export(project_id: str, job_id: str):
    try:
        job = production.public_job(project_id, job_id)
        path = production._jobs_dir(job_id) / "audiobook.m4b"
        if job["status"] != "completed" or not path.is_file():
            raise ValueError("Noch kein fertiges Hörbuch vorhanden.")
        return FileResponse(path, media_type="audio/mp4", filename="audiobook.m4b",
                            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/api/projects/{project_id}", dependencies=[Depends(require_auth)])
def delete_project(project_id: str):
    _project(project_id)
    # A 'paused' job can still have an in-flight segment being synthesized.
    # Protect both database deletion and on-disk cleanup from the GPU worker,
    # preview, segment retry and export.
    if not production._GPU.acquire(blocking=False):
        raise HTTPException(409, "Audioverarbeitung läuft. Bitte später erneut versuchen.")
    try:
        with _db() as db:
            db.execute("BEGIN IMMEDIATE")
            busy = db.execute(
                "SELECT 1 FROM studio_jobs WHERE project_id=? AND status IN ('running','exporting','queued')",
                (project_id,),
            ).fetchone()
            if busy:
                raise HTTPException(409, "Bitte laufende Aufträge zuerst abbrechen.")
            jobs = db.execute("SELECT id FROM studio_jobs WHERE project_id=?", (project_id,)).fetchall()
            db.execute("DELETE FROM studio_jobs WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM studio_projects WHERE id=?", (project_id,))
        shutil.rmtree(_project_dir(project_id))
        for job in jobs:
            shutil.rmtree(production._jobs_dir(job["id"]), ignore_errors=True)
        return {"deleted": True}
    finally:
        production._GPU.release()


@app.get("/")
def index():
    path = Path(__file__).resolve().parents[2] / "web" / "dist" / "index.html"
    if path.is_file():
        return FileResponse(path, media_type="text/html")
    return JSONResponse(
        {"message": "Frontend noch nicht gebaut. Bitte in web/ npm ci && npm run build ausführen."},
        status_code=503,
    )


@app.get("/assets/{asset_name}")
def asset(asset_name: str):
    # Only built assets, no arbitrary traversal or private file serving.
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", asset_name):
        raise HTTPException(404, "Datei nicht gefunden.")
    path = Path(__file__).resolve().parents[2] / "web" / "dist" / "assets" / asset_name
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, "Datei nicht gefunden.")
    return FileResponse(path)
