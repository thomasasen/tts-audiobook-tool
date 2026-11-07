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
    audit = project["content_audit"]
    assert audit["version"] == 2
    assert audit["status"] in {"review-required", "inventory-only"}
    assert audit["summary"]["spine_items"] >= len(chapters)
    assert "source_tables" in audit["summary"]
    assert isinstance(audit["resources"], list)
    assert isinstance(audit["skipped"], list)
    assert audit["review_notes"], "Audit must explain that source inventory is not editorial approval"
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


def test_b01_false_skip_restore_source_mapping_and_manual_speaker_versions(client):
    epub = build_epub(include_audit_cases=True)
    response = client.post(
        "/api/projects", headers=AUTH,
        files={"file": ("review-cases.epub", epub, "application/epub+zip")},
    )
    assert response.status_code == 201, response.text[:1500]
    project = response.json()
    audit = project["content_audit"]
    assert audit["version"] == 2
    assert audit["summary"]["mapping_coverage_percent"] == 100
    assert audit["summary"]["source_tables"] == 1
    assert audit["summary"]["source_mathml"] == 1
    assert audit["summary"]["source_footnotes"] == 1
    assert audit["summary"]["source_footnote_refs"] == 1
    assert {item["element_key"] for item in audit["review_elements"]} >= {
        "table:0", "math:0", "figure:0", "img:1",
    }
    copyright_skip = next(
        item for item in audit["skipped"]
        if item.get("href") == "OEBPS/copyright.xhtml"
    )
    assert copyright_skip["can_restore"] is True
    assert "Copyright" in copyright_skip["preview"]

    # A heuristic exclusion can be reversed explicitly while the project is still a draft.
    restored = client.put(
        f'/api/projects/{project["id"]}/source-overrides',
        headers=AUTH,
        json={"include_hrefs": ["OEBPS/copyright.xhtml"]},
    )
    assert restored.status_code == 200, restored.text[:1500]
    restored_project = restored.json()
    assert "Copyright" in [item["title"] for item in restored_project["chapters"]]
    assert restored_project["included_source_hrefs"] == ["OEBPS/copyright.xhtml"]
    resource = next(
        item for item in restored_project["content_audit"]["resources"]
        if item["href"] == "OEBPS/copyright.xhtml"
    )
    assert resource["status"] == "retained-by-user"

    reset = client.put(
        f'/api/projects/{project["id"]}/source-overrides',
        headers=AUTH,
        json={"include_hrefs": []},
    )
    assert reset.status_code == 200, reset.text[:1500]
    project = reset.json()
    assert "Copyright" not in [item["title"] for item in project["chapters"]]

    # Speaker text is deliberately user-authored and replaces the selected source element.
    speaker_texts = {
        "table:0": "Die Tabelle nennt den Messwert zweiundvierzig.",
        "math:0": "Die Formel lautet: x ist gleich fünf.",
        "figure:0": "Die Abbildung zeigt den Umsatzverlauf.",
        "img:1": "Das Firmenlogo ist dargestellt.",
    }
    for element_key, speaker_text in speaker_texts.items():
        element = next(
            item for item in project["content_audit"]["review_elements"]
            if item["element_key"] == element_key
        )
        assert element["can_edit"] is True
        saved_override = client.put(
            f'/api/projects/{project["id"]}/speaker-overrides',
            headers=AUTH,
            json={
                "source_href": element["source_href"],
                "element_key": element_key,
                "speaker_text": speaker_text,
            },
        )
        assert saved_override.status_code == 200, saved_override.text[:1500]
        project = saved_override.json()

    chapter = next(item for item in project["chapters"] if item["title"] == "Kapitel 1")
    for speaker_text in speaker_texts.values():
        assert speaker_text in chapter["preview"]
    assert len(project["speaker_overrides"]) == 4
    assert all(item["approved"] for item in project["speaker_overrides"])

    foreword = next(item for item in project["chapters"] if item["title"] == "Vorwort")
    saved = client.put(
        f'/api/projects/{project["id"]}/chapters',
        headers=AUTH,
        json={
            "start_index": foreword["index"],
            "excluded_indices": [item["index"] for item in project["chapters"] if item["is_toc"]],
        },
    )
    assert saved.status_code == 200, saved.text[:1500]
    blocked = client.put(
        f'/api/projects/{project["id"]}/speaker-overrides',
        headers=AUTH,
        json={
            "source_href": "OEBPS/chapter1.xhtml",
            "element_key": "table:0",
            "speaker_text": "Diese Änderung darf die gespeicherte Kapitelauswahl nicht still verändern.",
        },
    )
    assert blocked.status_code == 409
    folder = Path(os.environ["AUDIOBOOK_STUDIO_DATA_DIR"]) / "studio-projects" / project["id"]
    book = json.loads((folder / "engine" / "project_text.json").read_text(encoding="utf-8"))
    spoken = "\n".join(
        part["text"] for section in book["book"]["sections"]
        for group in section["phrase_groups"] for part in group["phrases"]
    )
    normalized_spoken = " ".join(spoken.split())
    for speaker_text in speaker_texts.values():
        assert " ".join(speaker_text.split()) in normalized_spoken
    assert "Rohwert 4711" not in spoken
    assert "Rohcaption" not in spoken


def test_protected_session_exposes_restart_nonce_but_public_health_does_not(client):
    assert "instance_id" not in client.get("/api/health").json()
    assert client.get("/api/session").status_code == 401
    response = client.get("/api/session", headers=AUTH)
    assert len(response.json()["instance_id"]) == 32
