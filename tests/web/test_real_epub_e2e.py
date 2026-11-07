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

from runpod.acceptance_epub import build_complex_epub, build_epub
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


def test_complex_epub_audit_reports_skips_and_restores_selected_sections(client):
    epub = build_complex_epub()
    response = client.post(
        "/api/projects", headers=AUTH,
        files={"file": ("complex-structure-test.epub", epub, "application/epub+zip")},
    )
    assert response.status_code == 201, response.text[:2000]
    project = response.json()

    from tts_audiobook_tool.text_ops.epub_extractor import EpubExtractor
    source_path = Path(os.environ["AUDIOBOOK_STUDIO_DATA_DIR"]) / "studio-projects" / project["id"] / "source.epub"
    load_result = EpubExtractor.load_source_chapters(str(source_path))
    assert load_result.skipped_sections, "load_source_chapters did not expose its structured omissions"
    chapter_result = EpubExtractor.extract_import_chapters(str(source_path))
    assert chapter_result.skipped_sections, "extract_import_chapters dropped structured omissions"

    skipped = project["skipped_sections"]
    assert skipped, f"Web catalogue dropped structured omissions: {project}"
    toc = next(item for item in skipped if item["category"] == "table-of-contents")
    imprint = next(item for item in skipped if item["category"] == "publication-metadata")
    assert toc["reason"] and toc["preview"]
    assert "Inhaltsverzeichnis" in toc["preview"]
    assert "publication metadata" in imprint["reason"]
    assert "Copyright" in imprint["preview"]
    assert imprint["recoverable"] is True

    chapters = project["chapters"]
    nav_prose = next(
        chapter for chapter in chapters
        if "Kurzer Hinweis für alle Leser" in chapter["preview"]
    )
    assert nav_prose["title"] == "Navigation"
    assert len({chapter["id"] for chapter in chapters}) == len(chapters)

    review_kinds = {item["kind"] for item in project["content_review_items"]}
    assert {"table", "formula", "figure", "footnote"}.issubset(review_kinds)
    assert project["coverage"]["complete"] is True
    assert project["coverage"]["sections_checked"] == len(chapters)
    assert all(item["complete"] for item in project["coverage"]["sections"])
    assert all(item["source_end"] >= item["source_start"] for item in project["coverage"]["sections"])
    assert all(item["phrase_group_end"] >= item["phrase_group_start"] for item in project["coverage"]["sections"])

    project_id = project["id"]
    folder = Path(os.environ["AUDIOBOOK_STUDIO_DATA_DIR"]) / "studio-projects" / project_id
    source_path = folder / "source.epub"
    assert source_path.read_bytes() == epub

    restored = client.put(
        f"/api/projects/{project_id}/skipped-sections",
        headers=AUTH,
        json={"included_ids": [imprint["id"]]},
    )
    assert restored.status_code == 200, restored.text[:2000]
    updated = restored.json()
    restored_imprint = next(
        item for item in updated["skipped_sections"] if item["id"] == imprint["id"]
    )
    assert restored_imprint["included"] is True
    assert any("Copyright" in chapter["preview"] for chapter in updated["chapters"])
    assert source_path.read_bytes() == epub, "Restoring content must never modify the original EPUB"

    # Existing logical chapters keep their identity after an earlier spine
    # resource is restored into the fallback reading section.
    nav_after_restore = next(
        chapter for chapter in updated["chapters"]
        if "Kurzer Hinweis für alle Leser" in chapter["preview"]
    )
    assert nav_after_restore["id"] == nav_prose["id"]
    selection = {
        "start_index": nav_after_restore["index"],
        "excluded_indices": [
            chapter["index"] for chapter in updated["chapters"] if chapter["is_toc"]
        ],
    }
    saved = client.put(
        f"/api/projects/{project_id}/chapters", headers=AUTH, json=selection
    )
    assert saved.status_code == 200, saved.text[:2000]
    assert saved.json()["status"] == "Vorbereitet"

    project_text = json.loads(
        (folder / "engine" / "project_text.json").read_text(encoding="utf-8")
    )
    sections = project_text["book"]["sections"]
    imported_text = "\\n".join(
        phrase["text"]
        for section in sections
        for group in section["phrase_groups"]
        for phrase in group["phrases"]
    )
    assert "Kurzer Hinweis für alle Leser" in imported_text
    assert "Copyright" in imported_text
    assert "Umsatz nach Quartal" in imported_text
    assert "1,2 Millionen Euro" in imported_text
    assert "Abbildung 1: Der Umsatz steigt im zweiten Quartal." in imported_text
    assert "Fußnote 1: Diese Erläuterung gehört zum vorherigen Satz." in imported_text
    assert "3. Erster geordneter Prüfschritt." in imported_text
    assert "4. Zweiter geordneter Prüfschritt." in imported_text
    assert "Unterpunkt mit einer zusätzlichen Bedingung." in imported_text
    assert "x=2" in imported_text.replace(" ", "")
    assert "* * *" in imported_text
    assert source_path.read_bytes() == epub

    hidden = client.put(
        f"/api/projects/{project_id}/skipped-sections",
        headers=AUTH,
        json={"included_ids": []},
    )
    assert hidden.status_code == 200, hidden.text[:2000]
    assert not any("Copyright" in chapter["preview"] for chapter in hidden.json()["chapters"])
    assert next(
        item for item in hidden.json()["skipped_sections"] if item["id"] == imprint["id"]
    )["included"] is False
    assert source_path.read_bytes() == epub


def test_protected_session_exposes_restart_nonce_but_public_health_does_not(client):
    assert "instance_id" not in client.get("/api/health").json()
    assert client.get("/api/session").status_code == 401
    response = client.get("/api/session", headers=AUTH)
    assert len(response.json()["instance_id"]) == 32
