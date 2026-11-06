"""Web Studio tests: authentication, safe archive handling and German chapter defaults.

These tests intentionally use no downloaded books, models or reference voices.
"""
from __future__ import annotations

import io
import os
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tts_audiobook_tool.web_studio import app as studio


TOKEN = "a" * 48


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIOBOOK_STUDIO_TOKEN", TOKEN)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(tmp_path / "private"))
    with TestClient(studio.app) as instance:
        yield instance


def authorized():
    return {"Authorization": f"Bearer {TOKEN}"}


def sample_epub(*, traverse: bool = False, bad_mime: bool = False) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("mimetype", "invalid" if bad_mime else "application/epub+zip")
        archive.writestr("../escape.txt" if traverse else "OEBPS/chapter.xhtml", "<p>Ein Text.</p>")
    return data.getvalue()


def test_health_public_but_projects_private(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/voices").status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/session", headers=authorized()).json()["language"] == "de"


def test_empty_library(client):
    assert client.get("/api/projects", headers=authorized()).json() == {"projects": []}


@pytest.mark.parametrize("bad", [
    sample_epub(traverse=True),
    sample_epub(bad_mime=True),
    b"not an epub",
])
def test_unsafe_epub_rejected_without_persistent_project(client, bad):
    response = client.post(
        "/api/projects", headers=authorized(),
        files={"file": ("sample.epub", bad, "application/epub+zip")},
    )
    assert response.status_code == 422
    assert client.get("/api/projects", headers=authorized()).json() == {"projects": []}


def test_unknown_project_and_path_traversal_blocked(client):
    assert client.get("/api/projects/invalid", headers=authorized()).status_code == 404
    assert client.get("/api/projects/../secret", headers=authorized()).status_code == 404


def test_upload_uses_existing_extractor_and_suggests_preface(client, monkeypatch):
    chapters = [
        SimpleNamespace(title="Inhaltsverzeichnis", text="1. Kapitel 2. Vorwort"),
        SimpleNamespace(title="Vorwort von einer Expertin", text="Willkommen zu dieser Untersuchung."),
        SimpleNamespace(title="Kapitel 1", text="Die Grundlagen sind wichtig."),
    ]
    stub = SimpleNamespace(chapters=chapters, book_title="Ein Fachbuch", warnings=[], significant_warnings=[])
    monkeypatch.setattr(studio, "_extract", lambda _: stub)

    response = client.post(
        "/api/projects", headers=authorized(),
        files={"file": ("buch.epub", sample_epub(), "application/epub+zip")},
    )
    assert response.status_code == 201
    saved = response.json()
    assert saved["suggested_start"] == 1
    assert saved["chapters"][0]["is_toc"] is True
    assert saved["chapters"][1]["preview"].startswith("Willkommen")
    project_id = saved["id"]
    loaded = client.get(f"/api/projects/{project_id}", headers=authorized()).json()
    assert loaded["chapters"] == saved["chapters"]
    assert len(client.get("/api/projects", headers=authorized()).json()["projects"]) == 1
    source = Path(os.environ["AUDIOBOOK_STUDIO_DATA_DIR"]) / "studio-projects" / project_id / "source.epub"
    assert source.is_file()


def test_premature_generation_not_exposed(client):
    assert client.get("/api/capabilities", headers=authorized()).json()["tts_generation"] is False
    assert client.post("/api/generate", headers=authorized()).status_code == 404
    result = client.get("/api/voices", headers=authorized()).json()
    assert result["ready"] == []
    assert result["candidates"][0]["status"] == "under_review"


def test_preface_fallback_and_toc_exclusion():
    chapters = [
        SimpleNamespace(title="Inhaltsverzeichnis", text="Ein Inhaltsverzeichnis"),
        SimpleNamespace(title="Einführung", text="Der erste Text."),
        SimpleNamespace(title="Kapitel 1", text="Der zweite Text."),
    ]
    result = studio._catalogue(SimpleNamespace(chapters=chapters, book_title="", warnings=[], significant_warnings=[]))
    assert result["suggested_start"] == 1
    assert result["chapters"][0]["is_toc"]
    assert result["title"] == "Unbenanntes Buch"


def test_no_token_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("AUDIOBOOK_STUDIO_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="AUDIOBOOK_STUDIO_TOKEN"):
        studio._require_token()
