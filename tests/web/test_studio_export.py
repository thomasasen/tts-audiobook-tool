"""Real AAC/M4B container test with synthetic audio, no GPU or copyrighted book."""
from __future__ import annotations

import io
import json
import math
import struct
import uuid
import wave

import av
import pytest
from fastapi.testclient import TestClient
from tts_audiobook_tool.web_studio import app as studio
from tts_audiobook_tool.web_studio import production


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIOBOOK_STUDIO_TOKEN", "x" * 48)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(tmp_path / "private"))
    with TestClient(studio.app):
        production.stop()
        yield


def _wave_file(path, seconds=1.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 24000
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        samples = [int(5000 * math.sin(2 * math.pi * 220 * i / rate))
                   for i in range(int(rate * seconds))]
        out.writeframes(struct.pack("<" + "h" * len(samples), *samples))


def test_production_exports_playable_m4b_with_two_chapter_titles(env):
    identifier = uuid.uuid4().hex
    job = uuid.uuid4().hex
    directory = studio._project_dir(identifier)
    directory.mkdir(parents=True)
    now = production._now()
    with studio._db() as db:
        db.execute("INSERT INTO studio_projects VALUES (?,?,?,?,?,?,?,?,?)", (
            identifier, "Management-Handbuch", "Audio bereit", "beispiel.epub", 2, 0,
            "[]", now, now,
        ))
        db.execute("INSERT INTO studio_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            job, identifier, production.VOICE_ID, "generated", 2, 2, "Kapitel 1", 2.,
            "", now, now,
        ))
    job_dir = production._jobs_dir(job)
    production._atomic_json(job_dir / "plan.json", {
        "segments": [
            {"chapter": 0, "title": "Vorwort", "text": "Guten Tag."},
            {"chapter": 1, "title": "Kapitel 1", "text": "Das ist eine Einführung."}
        ],
        "glossary": {},
    })
    _wave_file(job_dir / "audio" / "0000000.wav")
    _wave_file(job_dir / "audio" / "0000001.wav")
    exported = production.export_job(identifier, job)
    assert exported.suffix == ".m4b"
    assert exported.is_file() and exported.stat().st_size > 1000
    assert production._job(job)["status"] == "completed"

    # This opens and inspects the *actual* AAC/MP4 file. Mocks would not
    # validate playable media or persistent chapter titles.
    with av.open(str(exported)) as container:
        assert len(container.streams.audio) == 1
        assert container.metadata.get("title") == "Management-Handbuch"
        chapters = list(container.chapters())
        assert len(chapters) == 2
        assert [ch["metadata"]["title"] for ch in chapters] == ["Vorwort", "Kapitel 1"]
        duration = sum(frame.samples for packet in container.demux(audio=0)
                       for frame in packet.decode()) / container.streams.audio[0].rate
        assert duration >= 1.9


@pytest.mark.parametrize(("segments", "expected_titles"), [
    (
        [
            {"chapter": 2, "title": "Einleitung", "text": "Erster Abschnitt."},
            {"chapter": 2, "title": "Einleitung", "text": "Noch ein Absatz."},
            {"chapter": 3, "title": "Einleitung", "text": "Ein neues Kapitel."},
        ],
        ["Einleitung", "Einleitung"],
    ),
    (
        [
            {"chapter": 7, "title": "", "text": "Unbenannter Abschnitt eins."},
            {"chapter": 9, "title": "", "text": "Unbenannter Abschnitt zwei."},
        ],
        ["Kapitel 1", "Kapitel 2"],
    ),
    (
        [
            {"chapter": 1, "title": "Vorwort", "text": "Vorwort beginnt."},
            {"chapter": 4, "title": "Einleitung", "text": "Inhalt aus Kapitel vier."},
            {"chapter": 6, "title": "Einleitung", "text": "Inhalt aus Kapitel sechs."},
        ],
        ["Vorwort", "Einleitung", "Einleitung"],
    ),
])
def test_export_uses_chapter_ids_even_with_repeated_or_empty_titles(
        env, segments, expected_titles):
    """Real AAC decode and TOC markers, with synthetic 1-second WAV segments.

    The gaps in source chapter IDs emulate intentionally excluded spine
    sections; those missing sections must not produce empty markers.
    """
    identifier, job_id = uuid.uuid4().hex, uuid.uuid4().hex
    studio._project_dir(identifier).mkdir(parents=True)
    now = production._now()
    count = len(segments)
    with studio._db() as db:
        db.execute("INSERT INTO studio_projects VALUES (?,?,?,?,?,?,?,?,?)", (
            identifier, "Fachbuch ohne private Originale", "Audio bereit",
            "synthetisch.epub", 10, 1, "[]", now, now,
        ))
        db.execute("INSERT INTO studio_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            job_id, identifier, production.VOICE_ID, "generated",
            count, count, "", float(count), "", now, now,
        ))
    job_dir = production._jobs_dir(job_id)
    production._atomic_json(job_dir / "plan.json", {
        "segments": segments, "glossary": {},
    })
    for index in range(count):
        _wave_file(job_dir / "audio" / f"{index:07d}.wav")
    exported = production.export_job(identifier, job_id)
    assert exported.is_file()
    with av.open(str(exported)) as container:
        chapters = list(container.chapters())
        assert [item["metadata"]["title"] for item in chapters] == expected_titles
        assert len(chapters) == len(expected_titles)
        duration = sum(
            frame.samples for packet in container.demux(audio=0)
            for frame in packet.decode()
        ) / container.streams.audio[0].rate
        assert count - 0.1 <= duration <= count + 0.5, (
            "No source segment may be dropped or duplicated during export")
        if all("start" in item and "end" in item for item in chapters):
            assert chapters[0]["start"] == 0
            assert all(item["end"] > item["start"] for item in chapters)
            assert all(a["end"] <= b["start"]
                       for a, b in zip(chapters, chapters[1:]))


def test_legacy_export_chapter_key_fallback_without_schema_migration():
    assert production._export_chapter_key({"chapter": 0, "title": "Gleich"}) == ("section", 0)
    assert production._export_chapter_key({"chapter": 1, "title": "Gleich"}) == ("section", 1)
    assert production._export_chapter_key({"chapter_index": 3, "title": ""}) == ("section", 3)
    assert production._export_chapter_key({"title": "Alt"}) == ("legacy_title", "Alt")
