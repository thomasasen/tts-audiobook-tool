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
from typing import Annotated, Literal

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


def _extract(path: Path, include_skipped_ids: set[str] | None = None):
    # Use the application's canonical EPUB parser, including spine and
    # navigation logic. Never parse user-provided HTML in the web layer.
    from tts_audiobook_tool.project import Project
    from tts_audiobook_tool.text_ops.epub_extractor import EpubExtractor

    project = Project(language_code="de")
    result = EpubExtractor.import_epub(
        str(path),
        max_words=project.max_words,
        segmentation_strategy=project.segmentation_strategy,
        language_code="de",
        include_skipped_ids=include_skipped_ids,
    )
    if not result.chapters and not result.skipped_sections:
        raise ValueError("Das EPUB enthält keine auslesbaren Kapitel oder prüfbaren ausgelassenen Abschnitte.")
    if len(result.chapters) + len(result.skipped_sections) > MAX_CHAPTERS:
        raise ValueError("Das EPUB enthält zu viele Kapitel und ausgelassene Abschnitte.")
    return result


def _catalogue(result) -> dict:
    included_hrefs = {
        item.href.split("#", 1)[0]
        for item in getattr(result, "skipped_sections", [])
        if item.included
    }
    chapters = [
        {
            "id": getattr(chapter, "id", "") or f"chapter-{i}",
            "index": i,
            "title": (chapter.title or f"Abschnitt {i + 1}")[:250],
            "preview": chapter.text[:FIRST_TEXT_CHARS],
            "characters": len(chapter.text),
            "is_toc": (
                bool(TOC.fullmatch((chapter.title or "").strip()))
                and getattr(chapter, "href", "").split("#", 1)[0] not in included_hrefs
            ),
        }
        for i, chapter in enumerate(result.chapters)
    ]
    candidates = [c["index"] for c in chapters if not c["is_toc"]]
    skipped_sections = [
        {
            "id": item.id,
            "title": (item.title or item.href or "Ausgelassener Abschnitt")[:250],
            "href": item.href[:500],
            "reason": item.reason[:500],
            "preview": item.preview[:240],
            "category": item.category,
            "spine_index": item.spine_index,
            "recoverable": bool(item.recoverable),
            "included": bool(item.included),
        }
        for item in getattr(result, "skipped_sections", [])
    ]
    effective_included_ids = sorted(
        item["id"] for item in skipped_sections if item["included"]
    )
    if not chapters and not skipped_sections:
        raise ValueError("Nur nicht lesbare oder nicht wiederherstellbare Abschnitte gefunden.")
    prefaces = [i for i in candidates if PREFACE.match(chapters[i]["title"])]
    start = prefaces[0] if prefaces else (candidates[0] if candidates else 0)
    coverage_items = list(getattr(result, "coverage", []))
    review_items = [
        {
            "id": item.id,
            "kind": item.kind,
            "title": item.title,
            "href": item.href[:500],
            "reason": item.reason[:500],
            "preview": item.preview[:240],
            "count": item.count,
        }
        for item in getattr(result, "review_items", [])[:250]
    ]
    return {
        "title": (result.book_title or "Unbenanntes Buch")[:250],
        "chapters": chapters,
        "suggested_start": start,
        "warnings": [
            warning for warning in (result.significant_warnings or result.warnings)
            if not warning.startswith(("Skipped EPUB section:", "Skipped EPUB navigation document:"))
        ][:50],
        "skipped_sections": skipped_sections,
        "included_skipped_ids": effective_included_ids,
        "content_review_items": review_items,
        "coverage": {
            "complete": bool(coverage_items) and all(item.complete for item in coverage_items),
            "sections_checked": len(coverage_items),
            "sections_total": len(result.chapters),
            "phrase_groups": len(getattr(result, "phrase_groups", [])),
            "source_characters": sum(item.source_characters for item in coverage_items),
            "spoken_characters": sum(item.spoken_characters for item in coverage_items),
            "sections": [
                {
                    "chapter_id": item.chapter_id,
                    "title": item.title,
                    "href": item.href,
                    "source_start": item.source_start,
                    "source_end": item.source_end,
                    "phrase_group_start": item.phrase_group_start,
                    "phrase_group_end": item.phrase_group_end,
                    "source_characters": item.source_characters,
                    "spoken_characters": item.spoken_characters,
                    "complete": item.complete,
                }
                for item in coverage_items
            ],
        } if coverage_items else None,
    }


def _write_catalogue(directory: Path, catalogue: dict) -> None:
    path = directory / "chapters.json"
    temporary = directory / "chapters.json.tmp"
    temporary.write_text(json.dumps(catalogue, ensure_ascii=False), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


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


class SkippedSectionSelection(BaseModel):
    included_ids: list[str] = Field(default_factory=list, max_length=MAX_CHAPTERS)


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
        _write_catalogue(directory, catalogue)
        now = datetime.now(timezone.utc).isoformat()
        with _db() as db:
            db.execute(
                """INSERT INTO studio_projects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id, catalogue["title"], "Entwurf",
                    Path(file.filename).name[:255], len(catalogue["chapters"]),
                    catalogue["suggested_start"],
                    json.dumps(
                        [c["index"] for c in catalogue["chapters"] if c["is_toc"]]
                        if any(not c["is_toc"] for c in catalogue["chapters"]) else []
                    ),
                    now, now,
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
    return _public_project(row, catalogue)


@app.put("/api/projects/{project_id}/skipped-sections", dependencies=[Depends(require_auth)])
def update_skipped_sections(project_id: str, selection: SkippedSectionSelection):
    row = _project(project_id)
    if row["status"] not in {"Entwurf", "Vorbereitet"}:
        raise HTTPException(409, "Ausgelassene Abschnitte können nach Produktionsbeginn nicht mehr geändert werden.")
    directory = _project_dir(project_id)
    try:
        catalogue = json.loads((directory / "chapters.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(500, "Kapitelinformationen können nicht geladen werden.") from exc

    if len(set(selection.included_ids)) != len(selection.included_ids):
        raise HTTPException(422, "Die Auswahl enthält doppelte Abschnittskennungen.")
    if any(
        len(section_id) != 24
        or any(character not in "0123456789abcdef" for character in section_id)
        for section_id in selection.included_ids
    ):
        raise HTTPException(422, "Die Auswahl enthält eine ungültige Abschnittskennung.")
    recoverable_ids = {
        item["id"] for item in catalogue.get("skipped_sections", [])
        if item.get("recoverable")
    }
    requested_ids = set(selection.included_ids)
    if not requested_ids.issubset(recoverable_ids):
        raise HTTPException(422, "Mindestens ein Abschnitt kann nicht wieder aufgenommen werden.")

    old_chapters = catalogue.get("chapters", [])
    old_start = row["selected_start"]
    old_start_id = (
        old_chapters[old_start]["id"]
        if 0 <= old_start < len(old_chapters) else None
    )
    old_excluded = set(json.loads(row["excluded_json"]))
    old_excluded_ids = {
        old_chapters[index]["id"]
        for index in old_excluded
        if 0 <= index < len(old_chapters)
    }

    try:
        updated_catalogue = _catalogue(
            _extract(directory / "source.epub", requested_ids)
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    new_chapters = updated_catalogue["chapters"]
    new_indices = {chapter["id"]: chapter["index"] for chapter in new_chapters}
    old_chapter_ids = {chapter["id"] for chapter in old_chapters}
    newly_added_toc_ids = {
        chapter["id"] for chapter in new_chapters
        if chapter["is_toc"] and chapter["id"] not in old_chapter_ids
    } if any(not chapter["is_toc"] for chapter in new_chapters) else set()
    excluded_ids = old_excluded_ids | newly_added_toc_ids
    excluded_indices = sorted(
        new_indices[chapter_id]
        for chapter_id in excluded_ids
        if chapter_id in new_indices
    )
    selected_start = new_indices.get(old_start_id, updated_catalogue["suggested_start"])
    if selected_start in excluded_indices:
        selected_start = updated_catalogue["suggested_start"]
        if selected_start in excluded_indices and new_chapters:
            selected_start = next(
                (chapter["index"] for chapter in new_chapters
                 if chapter["index"] not in excluded_indices),
                selected_start,
            )

    _write_catalogue(directory, updated_catalogue)
    now = datetime.now(timezone.utc).isoformat()
    with _db() as db:
        db.execute(
            """UPDATE studio_projects
               SET chapter_count=?, selected_start=?, excluded_json=?, status='Entwurf', updated_at=?
               WHERE id=?""",
            (
                len(new_chapters), selected_start,
                json.dumps(excluded_indices), now, project_id,
            ),
        )
    return get_project(project_id)


@app.put("/api/projects/{project_id}/chapters", dependencies=[Depends(require_auth)])
def select_chapters(project_id: str, selection: ChapterSelection):
    row = _project(project_id)
    if row["status"] not in {"Entwurf", "Vorbereitet"}:
        raise HTTPException(409, "Das Projekt kann in seinem aktuellen Zustand nicht bearbeitet werden.")
    directory = _project_dir(project_id)
    catalogue = json.loads((directory / "chapters.json").read_text(encoding="utf-8"))
    chapters = catalogue["chapters"]
    legacy_toc_defaults: set[int] = set()
    if any(not chapter.get("id") for chapter in chapters):
        # Upgrade catalogues created before stable logical-chapter IDs were
        # introduced. Preserve the user's index-based selection only when the
        # current parser still produces the same chapter count.
        try:
            refreshed_catalogue = _catalogue(_extract(directory / "source.epub"))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if len(refreshed_catalogue["chapters"]) != len(chapters):
            raise HTTPException(
                409,
                "Die alte Kapitelübersicht ist nicht mehr deckungsgleich. Bitte das EPUB neu importieren.",
            )
        catalogue = refreshed_catalogue
        chapters = catalogue["chapters"]
        if any(not chapter["is_toc"] for chapter in chapters):
            # Older releases always excluded TOC chapters at save time, even
            # when excluded_json only stored the user's other exclusions.
            legacy_toc_defaults = {
                chapter["index"] for chapter in chapters if chapter["is_toc"]
            }
        _write_catalogue(directory, catalogue)
    if selection.start_index >= len(chapters):
        raise HTTPException(422, "Ungültiger Vorlesebeginn.")
    legacy_toc_defaults.discard(selection.start_index)
    excluded = set(selection.excluded_indices) | legacy_toc_defaults
    if any(i < 0 or i >= len(chapters) for i in excluded):
        raise HTTPException(422, "Ungültige Kapitelauswahl.")
    if selection.start_index in excluded:
        raise HTTPException(422, "Der erste vorgelesene Abschnitt darf nicht ausgeschlossen sein.")
    included = [i for i in range(selection.start_index, len(chapters)) if i not in excluded]
    if not included or included[0] != selection.start_index:
        raise HTTPException(422, "Mindestens das Startkapitel muss enthalten sein.")

    # Import and persist with the existing project schema, not a new book
    # representation. No TTS execution is triggered by this operation.
    from tts_audiobook_tool.project import Project
    from tts_audiobook_tool.project_support.project_text_io_util import ProjectTextIOUtil
    from tts_audiobook_tool.text_ops.epub_extractor import EpubExtractor

    try:
        project_defaults = Project(language_code="de")
        included_skipped_ids = set(catalogue.get("included_skipped_ids", []))
        book = EpubExtractor.import_epub(
            str(directory / "source.epub"),
            max_words=project_defaults.max_words,
            segmentation_strategy=project_defaults.segmentation_strategy,
            language_code="de",
            include_skipped_ids=included_skipped_ids,
        )
        if [chapter.id for chapter in book.chapters] != [chapter["id"] for chapter in chapters]:
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


@app.post("/api/voices/preview/start", dependencies=[Depends(require_auth)], status_code=202)
def start_preview(options: PreviewOptions = Body(default=PreviewOptions())):
    """Fast response: model load and inference continue in a single background worker."""
    try:
        return production.start_sample(duration_seconds=options.duration_seconds)
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


@app.get("/api/capabilities", dependencies=[Depends(require_auth)])
def capabilities():
    # Installation and explicit approval are independent states. The Web
    # Studio starts before the asynchronous Thorsten installation completes;
    # compute both on EVERY request rather than caching startup state.
    from .production import _model_identity
    installed = _model_identity() is not None
    approved = installed and production.voice_ready()
    if approved:
        reason = "Thorsten-CosyVoice3 ist installiert und nach Hörprobe freigegeben."
    elif installed:
        reason = (
            "Thorsten-CosyVoice3 ist installiert. Bitte eine echte Hörprobe "
            "erzeugen, vollständig anhören und anschließend freigeben."
        )
    else:
        reason = (
            "Thorsten-CosyVoice3 ist noch nicht vollständig installiert oder "
            "konnte nicht verifiziert werden. Die Hörprobe ist bis dahin gesperrt."
        )
    return {
        "epub_import": True,
        "chapter_selection": True,
        "tts_generation": approved,
        "audio_export": True,
        "voice_installed": installed,
        "voice_approved": approved,
        "voice_preview_available": installed,
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
