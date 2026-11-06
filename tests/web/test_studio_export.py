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
        assert [ch.metadata["title"] for ch in chapters] == ["Vorwort", "Kapitel 1"]
        duration = sum(frame.samples for packet in container.demux(audio=0)
                       for frame in packet.decode()) / container.streams.audio[0].rate
        assert duration >= 1.9
