"""Genuine German EPUB → canonical Project V5 regression test.

Unlike the light, mocked web tests, this exercises the real EbookLib parser,
spine/navigation ordering, segmentation and project persistence.
All EPUB prose is self-authored and generated in memory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runpod.acceptance_epub import build_epub
from tts_audiobook_tool.web_studio import app as studio
from tts_audiobook_tool.web_studio import production


TOKEN = "integration-test-only-" + "r" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIOBOOK_STUDIO_TOKEN", TOKEN)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AUDIOBOOK_STUDIO_ASR_MODEL", raising=False)
    with TestClient(studio.app) as target:
        yield target


def test_real_epub_import_foreword_selection_project_v5_and_queue(client, monkeypatch):
    epub = build_epub(repeat=2)
    response = client.post("/api/projects", headers=AUTH,
                           files={"file": ("original-test.epub", epub, "application/epub+zip")})
    assert response.status_code == 201, response.text[:1500]
    project = response.json()
    chapters = project["chapters"]
    titles = [c["title"] for c in chapters]
    assert "Vorwort" in titles, titles
    assert "Kapitel 1" in titles, titles
    assert "Kapitel 2" in titles, titles
    foreword = next(c for c in chapters if c["title"] == "Vorwort")
    assert project["suggested_start"] == foreword["index"]
    assert "Dieses Testbuch" in foreword["preview"]
    assert not any(c["is_toc"] and c["index"] == foreword["index"] for c in chapters)

    # Manually choose the first actual spoken section, excluding all TOCs.
    selection = {
        "start_index": foreword["index"],
        "excluded_indices": [c["index"] for c in chapters if c["is_toc"]],
    }
    saved = client.put(f'/api/projects/{project["id"]}/chapters', headers=AUTH,
                       json=selection)
    assert saved.status_code == 200, saved.text[:1500]
    assert saved.json()["status"] == "Vorbereitet"
    folder = Path(os.environ["AUDIOBOOK_STUDIO_DATA_DIR"]) / "studio-projects" / project["id"]
    assert (folder / "engine" / "project.json").is_file()
    book = json.loads((folder / "engine" / "project_text.json").read_text(encoding="utf-8"))
    assert book["format"] == "book.v3"
    sections = book["book"]["sections"]
    assert [s["title"] for s in sections] == ["Vorwort", "Kapitel 1", "Kapitel 2"]
    text = "\n".join(
        part["text"] for section in sections for group in section["phrase_groups"]
        for part in group["phrases"]
    )
    assert "Testbuch" in text and "Bedarfsanalyse" in text
    assert "Vorwort · Kapitel 1" not in text, "Inhaltsverzeichnis must not be spoken"

    # Verify that the new job engine uses exactly the canonical saved book.
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    production._STOP.set()  # prevent actual model inference; not a GPU test
    response = client.post(f'/api/projects/{project["id"]}/jobs', headers=AUTH)
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["total"] == len(production._manifest(project["id"]))
    plan_path = production._jobs_dir(job["id"]) / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert {item["title"] for item in plan["segments"]} == {"Vorwort", "Kapitel 1", "Kapitel 2"}
    assert plan["voice"] == production.VOICE_ID


def test_protected_session_exposes_restart_nonce_but_public_health_does_not(client):
    assert "instance_id" not in client.get("/api/health").json()
    assert client.get("/api/session").status_code == 401
    response = client.get("/api/session", headers=AUTH)
    assert len(response.json()["instance_id"]) == 32
