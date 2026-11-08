"""B03 regression tests. All text is synthetic; no GPU, books or model weights."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import struct
import uuid
import wave

import pytest
from fastapi.testclient import TestClient

from tts_audiobook_tool.web_studio import app as studio
from tts_audiobook_tool.web_studio import narration_plan as np
from tts_audiobook_tool.web_studio import production


def _book(chapters):
    return {"format": "book.v3", "book": {"title": "Synthetisches Fachbuch", "sections": [
        {"title": title, "phrase_groups": [
            {"phrases": [{"text": text, "reason": reason}]}
            for text, reason in groups
        ]}
        for title, groups in chapters
    ]}}


def _fixture(tmp_path, *, chapters=None, source_indices=None, mapping=True):
    if chapters is None:
        chapters = [
            ("Einleitung", [("1,25 € sind relevant. ", "s"), ("ARR ist eine Kennzahl.", "p")]),
            ("Einleitung", [("ARR ist eine Kennzahl.", "xx")]),
        ]
    if source_indices is None:
        source_indices = list(range(len(chapters)))
    folder = tmp_path / "project"
    (folder / "engine").mkdir(parents=True)
    (folder / "engine" / "project_text.json").write_text(
        json.dumps(_book(chapters), ensure_ascii=False), encoding="utf-8",
    )
    raw_texts = ["".join(text for text, _ in groups) for _, groups in chapters]
    (folder / "engine" / "project_text_raw.txt").write_text(
        "\n\n".join(raw_texts), encoding="utf-8",
    )
    catalogue = {
        "chapters": [{"index": i, "title": title, "characters": len(raw_texts[i])}
                     for i, (title, _) in enumerate(chapters)],
        "content_audit": {"source_mapping": [
            {"chapter_index": i, "source_href": f"OEBPS/c{i}.xhtml",
             "source_start": 0, "source_end": len(txt),
             "output_start": 0, "output_end": len(txt),
             "mapping_status": "mapped"}
            for i, txt in enumerate(raw_texts) if txt and mapping
        ]},
    }
    (folder / "chapters.json").write_text(json.dumps(catalogue), encoding="utf-8")
    return folder, catalogue


def _plan(folder, source_indices=None, *, glossary=None, created_at="first"):
    if source_indices is None:
        sections = json.loads((folder / "engine" / "project_text.json").read_text())["book"]["sections"]
        source_indices = list(range(len(sections)))
    return np.build_narration_plan(
        "synthetic", folder, source_indices, glossary or {}, production.VOICE_ID,
        "model-1", production.DE_NORMALIZER_VERSION, production._narration_prompt,
        created_at=created_at,
    )


def test_plan_is_deterministic_has_b01_provenance_and_b02_prompts(tmp_path):
    folder, _ = _fixture(tmp_path)
    first = _plan(folder, glossary={"ARR": "A R R"})
    second = _plan(folder, glossary={"ARR": "A R R"}, created_at="other")
    assert first["version"] == 3 and first["schema"] == np.SCHEMA
    assert first["plan_fingerprint"] == second["plan_fingerprint"]
    assert first["segments"] == second["segments"]
    assert first["created_at"] != second["created_at"]
    assert [s["position"] for s in first["segments"]] == [0, 1, 2]
    assert [s["chapter_id"] for s in first["segments"]] == [
        "epub-chapter:000000", "epub-chapter:000000", "epub-chapter:000001"
    ]
    assert len(set(s["unit_id"] for s in first["segments"])) == 3
    assert first["segments"][1]["text"] == first["segments"][2]["text"]
    assert first["segments"][1]["unit_id"] != first["segments"][2]["unit_id"]
    for s in first["segments"]:
        assert s["speech_text"] == production._narration_prompt(
            s["text"], first["glossary"], production.DE_NORMALIZER_VERSION
        )
        assert s["source"]["mapping_status"] == "mapped"
        assert s["source"]["epub_spans"][0]["source_href"].endswith(
            f"c{s['chapter_source_index']}.xhtml"
        )
        assert s["original_text"] == "".join(p["text"] for p in s["source"]["phrases"])
    assert first["segments"][0]["original_text"].endswith(" ")
    assert first["segments"][0]["text"] == "1,25 € sind relevant."
    assert first["segments"][0]["speech_text"] != first["segments"][0]["text"]
    assert np.validate_narration_plan(first)


def test_types_reasons_and_canonical_group_boundaries(tmp_path):
    chapters = [("Vorwort", [
        ("Zur Einleitung\n", "h"),
        ("• Punkt eins.\n", "p"),
        ("Der Inhalt folgt. ", "s"),
        ("Letzter Satz.", "xx"),
    ])]
    folder, _ = _fixture(tmp_path, chapters=chapters)
    plan = _plan(folder)
    assert [s["section_type"] for s in plan["segments"]] == [
        "heading", "list", "prose", "prose"
    ]
    assert [s["boundary_reason"] for s in plan["segments"]] == [
        "heading", "paragraph", "sentence", "chapter"
    ]
    assert all(s["pause"]["duration_ms"] is None for s in plan["segments"])


def test_selected_preface_and_nonconsecutive_source_indices(tmp_path):
    source_chapters = [
        ("TOC", [("Inhalt.", "xx")]),
        ("Vorwort", [("Der Anfang.", "xx")]),
        ("Entfallen", [("Nicht sprechen.", "xx")]),
        ("Einleitung", [("Es geht weiter.", "xx")]),
    ]
    selected = [source_chapters[1], source_chapters[3]]
    folder, _ = _fixture(tmp_path, chapters=selected)
    cat = {
        "chapters": [{"index": i, "title": name, "characters": len("".join(t for t, _ in groups))}
                     for i, (name, groups) in enumerate(source_chapters)],
        "content_audit": {"source_mapping": [
            {"chapter_index": i, "source_href": f"x{i}.xhtml",
             "source_start": 0, "source_end": len(text), "output_start": 0,
             "output_end": len(text), "mapping_status": "mapped"}
            for i in (1, 3)
            for text in ["".join(t for t, _ in source_chapters[i][1])]
        ]},
    }
    (folder / "chapters.json").write_text(json.dumps(cat), encoding="utf-8")
    plan = _plan(folder, [1, 3])
    assert [s["chapter_source_index"] for s in plan["segments"]] == [1, 3]
    assert [s["chapter_id"] for s in plan["segments"]] == [
        "epub-chapter:000001", "epub-chapter:000003"
    ]
    assert "Nicht sprechen" not in json.dumps(plan, ensure_ascii=False)


def test_empty_group_and_missing_heading_are_not_invented(tmp_path):
    folder, _ = _fixture(tmp_path, chapters=[
        ("", [("", "undefined"), ("Ein echtes Wort.", "xx")]),
        ("", [(" ", "p"), ("Noch ein Wort.", "xx")]),
    ])
    plan = _plan(folder)
    assert len(plan["segments"]) == 2
    assert [x["title"] for x in plan["segments"]] == ["Kapitel 1", "Kapitel 2"]
    assert [x["text"] for x in plan["segments"]] == ["Ein echtes Wort.", "Noch ein Wort."]


@pytest.mark.parametrize(("mutation", "message"), [
    ("loss", "Auslassung"),
    ("duplicate", "Dublette"),
    ("reorder", "vertauschter"),
])
def test_raw_chapter_text_cannot_silently_lose_duplicate_or_reorder(tmp_path, mutation, message):
    folder, _ = _fixture(tmp_path)
    path = folder / "engine" / "project_text.json"
    book = json.loads(path.read_text())
    groups = book["book"]["sections"][0]["phrase_groups"]
    if mutation == "loss":
        groups.pop()
    elif mutation == "duplicate":
        groups.append(copy.deepcopy(groups[-1]))
    else:
        groups.reverse()
    path.write_text(json.dumps(book), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        _plan(folder)


@pytest.mark.parametrize("change", ["missing", "overlap", "unresolved", "bad_source", "reversed"])
def test_b01_source_mapping_is_checked_not_just_percent_report(tmp_path, change):
    folder, cat = _fixture(tmp_path)
    spans = cat["content_audit"]["source_mapping"]
    if change == "missing":
        spans.pop(0)
    elif change == "overlap":
        spans.insert(0, dict(spans[0]))
    elif change == "unresolved":
        spans[0]["mapping_status"] = "unresolved"
    elif change == "bad_source":
        spans[0]["source_start"] = None
    else:
        spans[0]["output_start"], spans[0]["output_end"] = 14, 3
    (folder / "chapters.json").write_text(json.dumps(cat), encoding="utf-8")
    with pytest.raises(ValueError, match="Quelltext|Quelltextspanne|Abdeckung"):
        _plan(folder)



def test_b01_audit_with_missing_mapping_is_not_disguised_as_legacy(tmp_path):
    folder, cat = _fixture(tmp_path, mapping=False)
    cat["content_audit"]["version"] = 2
    (folder / "chapters.json").write_text(json.dumps(cat), encoding="utf-8")
    with pytest.raises(ValueError, match="B01-Quelltextzuordnung fehlt"):
        _plan(folder)



def test_same_epub_source_range_in_two_selected_chapters_is_a_real_duplicate(tmp_path):
    folder, cat = _fixture(tmp_path)
    mapping = cat["content_audit"]["source_mapping"]
    mapping[1]["source_href"] = mapping[0]["source_href"]
    mapping[1]["source_start"] = mapping[0]["source_start"]
    mapping[1]["source_end"] = mapping[1]["source_start"] + mapping[1]["output_end"] - mapping[1]["output_start"]
    (folder / "chapters.json").write_text(json.dumps(cat), encoding="utf-8")
    with pytest.raises(ValueError, match="EPUB-Quelltextspanne wurde mehrfach"):
        _plan(folder)


def test_same_words_at_different_source_offsets_are_not_duplicates(tmp_path):
    folder, cat = _fixture(tmp_path)
    mapping = cat["content_audit"]["source_mapping"]
    mapping[1]["source_href"] = mapping[0]["source_href"]
    mapping[1]["source_start"] = mapping[0]["source_end"] + 20
    mapping[1]["source_end"] = mapping[1]["source_start"] + mapping[1]["output_end"] - mapping[1]["output_start"]
    (folder / "chapters.json").write_text(json.dumps(cat), encoding="utf-8")
    plan = _plan(folder)
    assert plan["segments"][1]["text"] == plan["segments"][2]["text"]
    assert np.validate_narration_plan(plan)


def test_pre_b01_project_falls_back_explicitly_to_book_source(tmp_path):
    folder, _ = _fixture(tmp_path, mapping=False)
    plan = _plan(folder)
    assert all(s["source"]["mapping_status"] == "book-only" for s in plan["segments"])
    assert all(s["source"]["epub_spans"] == [] for s in plan["segments"])


@pytest.mark.parametrize("field", [
    "version", "schema", "position", "unit_id", "text", "speech_text",
    "source_start", "reason", "config", "reorder", "drop",
])
def test_corrupt_plan_fails_closed(tmp_path, field):
    folder, _ = _fixture(tmp_path)
    plan = _plan(folder)
    if field == "version":
        plan["version"] = 984
    elif field == "schema":
        plan["schema"] = "unknown"
    elif field == "position":
        plan["segments"][0]["position"] = 3
    elif field == "unit_id":
        plan["segments"][1]["unit_id"] = plan["segments"][0]["unit_id"]
    elif field == "text":
        plan["segments"][0]["text"] = "falscher Text"
    elif field == "speech_text":
        plan["segments"][0]["speech_text"] = "falscher Prompt"
    elif field == "source_start":
        plan["segments"][0]["source"]["phrases"][0]["start"] = 2
    elif field == "reason":
        plan["segments"][0]["source"]["phrases"][0]["reason"] = "bad"
    elif field == "config":
        plan["glossary"]["ARR"] = "gefälscht"
    elif field == "reorder":
        plan["segments"].reverse()
    else:
        plan["segments"].pop()
    with pytest.raises(ValueError, match="NarrationPlan|Version"):
        np.validate_narration_plan(plan)


def test_fingerprint_config_and_unit_changes_are_scoped(tmp_path):
    folder, _ = _fixture(tmp_path)
    a = _plan(folder)
    b = _plan(folder, glossary={"ARR": "A R R"})
    assert a["config_fingerprint"] != b["config_fingerprint"]
    assert a["content_fingerprint"] != b["content_fingerprint"]
    assert np.segment_fingerprint(a["segments"][0], a) != np.segment_fingerprint(b["segments"][0], b)
    c = copy.deepcopy(a)
    c["model_identity"] = "model-2"
    c["config_fingerprint"] = np._digest({
        "voice": c["voice"], "model_identity": c["model_identity"],
        "audio": c["audio"], "glossary": c["glossary"],
        "normalizer_version": c["normalizer_version"],
    })
    assert np.segment_fingerprint(a["segments"][0], a) != np.segment_fingerprint(c["segments"][0], c)


def test_project_file_change_is_caught_before_resume(tmp_path):
    folder, _ = _fixture(tmp_path)
    plan = _plan(folder)
    payload = json.loads((folder / "engine" / "project_text.json").read_text())
    payload["book"]["sections"][0]["phrase_groups"][0]["phrases"][0]["text"] = "Anders."
    (folder / "engine" / "project_text.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="verändert"):
        np.assert_book_unchanged(folder, plan)


def test_legacy_hashing_is_byte_identical_and_not_migrated():
    segment = {"text": "ARR zählt."}
    plan = {"version": 2, "segments": [segment], "glossary": {"ARR": "A R R"},
            "model_identity": "id", "normalizer_version": production.DE_V2_NORMALIZER_VERSION}
    expected = hashlib.sha256((
        "ARR zählt.\0" + json.dumps({"ARR": "A R R"}, sort_keys=True)
        + "\0" + production.VOICE_ID + "\0id\0"
        + production.DE_V2_NORMALIZER_VERSION
    ).encode()).hexdigest()
    assert not np.validate_narration_plan(plan)
    assert production._segment_fingerprint(segment, plan) == expected
    del plan["normalizer_version"]
    expected = hashlib.sha256((
        "ARR zählt.\0" + json.dumps({"ARR": "A R R"}, sort_keys=True)
        + "\0" + production.VOICE_ID + "\0id"
    ).encode()).hexdigest()
    assert production._segment_fingerprint(segment, plan) == expected


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIOBOOK_STUDIO_TOKEN", "unit-test-" + "x" * 48)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(tmp_path / "storage"))
    with TestClient(studio.app) as client:
        production.stop()
        yield client


def _prepared_studio_project(project_id):
    directory = studio._project_dir(project_id)
    (directory / "engine").mkdir(parents=True, exist_ok=True)
    (directory / "engine" / "project_text.json").write_text(
        json.dumps(_book([("Vorwort", [("1,25 € und ARR.", "xx")])])), encoding="utf-8")
    (directory / "engine" / "project.json").write_text(
        json.dumps({"version": 5, "word_substitutions": {"ARR": "A R R"}}), encoding="utf-8")
    now = production._now()
    with studio._db() as db:
        db.execute("INSERT INTO studio_projects VALUES (?,?,?,?,?,?,?,?,?)", (
            project_id, "Test", "Vorbereitet", "test.epub", 1, 0, "[]", now, now,
        ))


def _tone(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 24000
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        samples = [int(2500 * math.sin(2 * math.pi * 220 * i / rate))
                   for i in range(rate)]
        output.writeframes(struct.pack("<" + "h" * len(samples), *samples))


def test_new_studio_job_synthesizes_the_frozen_prompt_and_reuses_safe_checkpoint(
        env, monkeypatch):
    project_id = uuid.uuid4().hex
    _prepared_studio_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    monkeypatch.setattr(production, "_model_identity", lambda: "model-1")
    job = production.create_job(project_id)
    job_id = job["id"]
    plan = json.loads((production._jobs_dir(job_id) / "plan.json").read_text())
    assert np.validate_narration_plan(plan)
    assert job["total"] == 1
    assert plan["segments"][0]["speech_text"] != plan["segments"][0]["text"]
    used_prompts = []

    def fake_synthesize(text, path):
        used_prompts.append(text)
        _tone(path)
        return production._valid_audio(path)

    monkeypatch.setattr(production, "_synthesize", fake_synthesize)
    monkeypatch.setattr(production, "_audio_quality", lambda *a: {"status": "not_checked"})
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running' WHERE id=?", (job_id,))
    production._STOP.clear()
    production._run(job_id)
    assert used_prompts == [plan["segments"][0]["speech_text"]]
    sidecar = json.loads((production._jobs_dir(job_id) / "audio" / "0000000.json").read_text())
    assert sidecar["unit_id"] == plan["segments"][0]["unit_id"]
    assert sidecar["plan_schema"] == np.SCHEMA
    assert sidecar["fingerprint"] == production._segment_fingerprint(plan["segments"][0], plan)

    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running', done=0 WHERE id=?", (job_id,))
    production._run(job_id)
    assert used_prompts == [plan["segments"][0]["speech_text"]], "valid WAV must be reused"
    assert production._job(job_id)["status"] == "generated"



def test_valid_but_changed_wav_bytes_cannot_be_reused(env, monkeypatch):
    project_id = uuid.uuid4().hex
    _prepared_studio_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    monkeypatch.setattr(production, "_model_identity", lambda: "model-1")
    job = production.create_job(project_id)
    prompts = []

    def synthesize(text, path):
        prompts.append(text)
        _tone(path)
        return production._valid_audio(path)

    monkeypatch.setattr(production, "_synthesize", synthesize)
    monkeypatch.setattr(production, "_audio_quality", lambda *a: {"status": "not_checked"})
    production._STOP.clear()
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running' WHERE id=?", (job["id"],))
    production._run(job["id"])
    assert len(prompts) == 1

    # Produce an equally valid WAV with changed bytes and valid duration.
    path = production._jobs_dir(job["id"]) / "audio" / "0000000.wav"
    data = bytearray(path.read_bytes())
    data[-1] ^= 0x01
    path.write_bytes(data)
    assert production._valid_audio(path) > 0
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running',done=0 WHERE id=?", (job["id"],))
    production._run(job["id"])
    assert len(prompts) == 2, "WAVs with mismatched SHA-256 must be regenerated"


def test_unknown_or_corrupt_plan_rejected_before_worker_inference(env, monkeypatch):
    project_id = uuid.uuid4().hex
    _prepared_studio_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    monkeypatch.setattr(production, "_model_identity", lambda: "model-1")
    job = production.create_job(project_id)
    path = production._jobs_dir(job["id"]) / "plan.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["version"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")
    production._STOP.clear()
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running' WHERE id=?", (job["id"],))
    with pytest.raises(ValueError, match="Version"):
        production._run(job["id"])

def test_changed_glossary_fails_resume_instead_of_mixing_prompts(env, monkeypatch):
    project_id = uuid.uuid4().hex
    _prepared_studio_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    monkeypatch.setattr(production, "_model_identity", lambda: "model-1")
    job = production.create_job(project_id)
    project_json = studio._project_dir(project_id) / "engine" / "project.json"
    project_json.write_text(
        json.dumps({"version": 5, "word_substitutions": {"ARR": "anderer Prompt"}}),
        encoding="utf-8",
    )
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running' WHERE id=?", (job["id"],))
    with pytest.raises(ValueError, match="glossar"):
        production._run(job["id"])


def test_export_rejects_missing_or_wrong_plan_checkpoint(env, monkeypatch):
    project_id = uuid.uuid4().hex
    _prepared_studio_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    monkeypatch.setattr(production, "_model_identity", lambda: "model-1")
    job = production.create_job(project_id)
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='generated', done=1 WHERE id=?", (job["id"],))
    _tone(production._jobs_dir(job["id"]) / "audio" / "0000000.wav")
    with pytest.raises(ValueError, match="Checkpoint"):
        production.export_job(project_id, job["id"])
