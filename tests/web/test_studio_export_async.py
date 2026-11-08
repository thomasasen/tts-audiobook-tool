"""Timeout-safe M4B jobs and authenticated chapter archive regressions.

Uses synthetic job records and fake media bytes; never requires a GPU or book.
"""
from __future__ import annotations

import io
import json
import threading
import time
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

from tts_audiobook_tool.web_studio import app as studio
from tts_audiobook_tool.web_studio import production


TOKEN = "x" * 48
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIOBOOK_STUDIO_TOKEN", TOKEN)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(tmp_path / "private"))
    with TestClient(studio.app) as instance:
        production.stop()
        # Tests exercise the export-only thread without the TTS loop.
        production._STOP.clear()
        yield instance


def seed_job(*, status="generated", titles=("Einführung", "Nächstes Kapitel")):
    project_id, job_id = uuid.uuid4().hex, uuid.uuid4().hex
    studio._project_dir(project_id).mkdir(parents=True)
    now = production._now()
    with studio._db() as db:
        db.execute("INSERT INTO studio_projects VALUES (?,?,?,?,?,?,?,?,?)", (
            project_id, "Testbuch", "Audio bereit", "test.epub", len(titles), 0,
            "[]", now, now,
        ))
        db.execute("INSERT INTO studio_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            job_id, project_id, production.VOICE_ID, status, len(titles),
            len(titles), titles[-1], float(len(titles)), "", now, now,
        ))
    directory = production._jobs_dir(job_id)
    production._atomic_json(directory / "plan.json", {
        "segments": [{"chapter": i, "title": title, "text": title}
                     for i, title in enumerate(titles)],
        "glossary": {},
    })
    return project_id, job_id


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        threading.Event().wait(0.02)
    raise AssertionError("Timed out waiting for background export state")


def test_async_export_returns_202_and_is_duplicate_safe(client, monkeypatch):
    project_id, job_id = seed_job()
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def slow_export(proj, job, *, _claimed=False):
        assert _claimed is True
        calls.append((proj, job))
        entered.set()
        assert release.wait(4), "test did not release the export"
        path = production._jobs_dir(job) / "audiobook.m4b"
        path.write_bytes(b"mocked-export")
        production._write_export_progress(job, 2, 2)
        with studio._db() as db:
            db.execute("UPDATE studio_jobs SET status='completed' WHERE id=?", (job,))
        return path

    monkeypatch.setattr(production, "export_job", slow_export)
    route = f"/api/projects/{project_id}/jobs/{job_id}/export"
    try:
        assert client.post(route).status_code == 401
        first = client.post(route, headers=AUTH)
        assert first.status_code == 202
        assert first.json()["status"] == "exporting"
        assert entered.wait(2)
        duplicate = client.post(route, headers=AUTH)
        assert duplicate.status_code == 202
        assert duplicate.json()["already_running"] is True
        assert len(calls) == 1
        activity = client.get(
            f"/api/projects/{project_id}/jobs/{job_id}/activity", headers=AUTH
        ).json()
        assert activity["export"] == {"processed": 0, "total": 2, "percent": 0}
    finally:
        release.set()
    wait_for(lambda: production._job(job_id)["status"] == "completed")
    assert len(calls) == 1
    assert client.post(route, headers=AUTH).status_code == 200
    assert client.get(f"/api/projects/{project_id}/jobs/{job_id}/activity",
                      headers=AUTH).json()["export"]["percent"] == 100


def test_failed_async_export_remains_retryable_without_leaking_error(client, monkeypatch):
    project_id, job_id = seed_job()
    called = threading.Event()

    def failed_export(*_args, **_kwargs):
        called.set()
        raise RuntimeError("private-secret-internal-error")

    monkeypatch.setattr(production, "export_job", failed_export)
    route = f"/api/projects/{project_id}/jobs/{job_id}/export"
    assert client.post(route, headers=AUTH).status_code == 202
    assert called.wait(2)
    wait_for(lambda: production._job(job_id)["status"] == "generated")
    job = production._job(job_id)
    assert job["error"] == "Export fehlgeschlagen"
    assert "private-secret" not in job["error"]
    assert client.post(route, headers=AUTH).status_code == 202
    wait_for(lambda: production._job(job_id)["status"] == "generated")


def test_restart_recovers_export_and_preserves_audio(client):
    project_id, job_id = seed_job(status="exporting")
    directory = production._jobs_dir(job_id)
    audio = directory / "audio" / "0000000.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"precious-audio")
    staged = directory / "audiobook.m4b.partial"
    staged.write_bytes(b"unfinished")
    production.init()
    assert production.public_job(project_id, job_id)["status"] == "generated"
    assert audio.read_bytes() == b"precious-audio"
    assert not staged.exists()


def test_chapter_names_are_safe_sortable_and_preserve_umlauts():
    name = production.chapter_download_name(0, 'Einführung / A:B? "Ziel"')
    assert name.startswith("001 - Einführung")
    assert name.endswith(".m4a")
    assert not any(c in name for c in r'/\:*?"<>|')
    assert production.chapter_download_name(1, "CON") == "002 - CON.m4a"
    assert production.chapter_download_name(2, "\u202e") == "003 - Kapitel.m4a"


def prepare_chapters(project_id, job_id):
    directory = production._jobs_dir(job_id)
    chapters = production._chapter_index(job_id)
    for chapter in chapters:
        i = chapter["index"]
        audio, meta = production._chapter_audio_paths(job_id, i)
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(bytes([65 + i]) * 1500)
        preview = production._chapter_preview_path(job_id, i)
        preview.write_bytes(b"p" * 1500)
        production._atomic_json(meta, {
            "version": production.CHAPTER_AUDIO_CACHE_VERSION,
            "fingerprint": chapter["fingerprint"],
            "start": chapter["start"], "end": chapter["end"],
            "byte_size": audio.stat().st_size,
            "preview_byte_size": preview.stat().st_size,
            "preview_duration": 1.0, "duration": 1.0,
        })
    return chapters


def test_chapter_archive_streams_complete_named_zip_without_temp_file(client):
    project_id, job_id = seed_job(status="completed", titles=("Einführung", "A/B: Test"))
    prepare_chapters(project_id, job_id)
    url = f"/api/projects/{project_id}/jobs/{job_id}/chapters/archive"
    assert client.get(url).status_code == 401
    result = client.get(url, headers=AUTH)
    assert result.status_code == 200
    assert result.headers["content-type"].startswith("application/zip")
    assert result.headers["cache-control"] == "no-store"
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        names = archive.namelist()
        assert names == ["001 - Einführung.m4a", "002 - A_B_ Test.m4a"]
        assert archive.read(names[0]) == b"A" * 1500
        assert archive.read(names[1]) == b"B" * 1500
        assert archive.testzip() is None
    assert not list(production._jobs_dir(job_id).glob("*.zip"))
    # The individual download's Content-Disposition also has the title.
    single = client.get(
        f"/api/projects/{project_id}/jobs/{job_id}/chapters/0/download",
        headers=AUTH,
    )
    assert single.status_code == 200
    assert "Einführung" in single.headers["content-disposition"] or "Einf%C3%BChrung" in single.headers["content-disposition"]


def test_archive_rejects_missing_chapter_or_unfinished_job(client):
    project_id, job_id = seed_job(status="generated")
    prepare_chapters(project_id, job_id)
    url = f"/api/projects/{project_id}/jobs/{job_id}/chapters/archive"
    assert client.get(url, headers=AUTH).status_code == 409
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='completed' WHERE id=?", (job_id,))
    production._chapter_audio_paths(job_id, 1)[0].unlink()
    assert client.get(url, headers=AUTH).status_code == 409
