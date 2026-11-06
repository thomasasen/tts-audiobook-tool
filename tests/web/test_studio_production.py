"""Offline studio regression coverage: credentials, queue state and existing book schema.

No model weights, cloned voices or user-owned books are needed for these tests.
"""
from __future__ import annotations

import json
import math
import struct
import uuid
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tts_audiobook_tool.web_studio import app as studio
from tts_audiobook_tool.web_studio import production


TOKEN = "test-only-" + "x" * 48
HEADERS = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIOBOOK_STUDIO_TOKEN", TOKEN)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_DATA_DIR", str(tmp_path / "secure"))
    monkeypatch.delenv("AUDIOBOOK_THORSTEN_SCRIPT", raising=False)
    monkeypatch.delenv("AUDIOBOOK_THORSTEN_PYTHON", raising=False)
    with TestClient(studio.app) as client:
        # Stop the worker before manipulating queue test states: assertions
        # stay deterministic and do not pretend to validate model inference.
        production.stop()
        yield client


def prepare_project(project_id: str):
    directory = studio._project_dir(project_id)
    (directory / "engine").mkdir(parents=True)
    (directory / "engine" / "project.json").write_text(
        json.dumps({"version": 5, "word_substitutions": {"ARR": "A R R"}}),
        encoding="utf-8",
    )
    (directory / "engine" / "project_text.json").write_text(
        json.dumps({
            "format": "book.v3",
            "book": {
                "title": "Ein Beispiel",
                "sections": [
                    {"title": "Vorwort", "phrase_groups": [
                        {"phrases": [{"text": "Die Pipeline ist wichtig."}]},
                        {"phrases": [{"text": "ARR ist eine Kennzahl."}]},
                    ]},
                    {"title": "Kapitel 1", "phrase_groups": [
                        {"phrases": [{"text": "Die Prognose folgt."}]},
                    ]},
                ],
            },
        }),
        encoding="utf-8",
    )
    now = production._now()
    with studio._db() as db:
        db.execute(
            "INSERT INTO studio_projects VALUES (?,?,?,?,?,?,?,?,?)",
            (project_id, "Ein Beispiel", "Vorbereitet", "beispiel.epub", 2, 0, "[]", now, now),
        )


def test_private_production_endpoints_fail_closed(env):
    project_id = uuid.uuid4().hex
    assert env.get(f"/api/projects/{project_id}/jobs").status_code == 401
    assert env.get(f"/api/projects/{project_id}/glossary").status_code == 401
    assert env.post(f"/api/projects/{project_id}/jobs").status_code == 401
    assert env.post("/api/voices/preview").status_code == 401
    assert env.post("/api/voices/approve").status_code == 401


def test_voice_is_not_available_without_an_engine_or_a_real_sample(env):
    voices = env.get("/api/voices", headers=HEADERS).json()
    assert voices["ready"] == []
    assert voices["candidates"][0]["status"] == "under_review"
    assert env.post("/api/voices/preview", headers=HEADERS).status_code == 409
    assert env.get("/api/capabilities", headers=HEADERS).json()["tts_generation"] is False


def test_canonical_book_manifest_and_project_glossary(env):
    identifier = uuid.uuid4().hex
    prepare_project(identifier)
    items = production._manifest(identifier)
    assert [item["title"] for item in items] == ["Vorwort", "Vorwort", "Kapitel 1"]
    assert [item["text"] for item in items] == [
        "Die Pipeline ist wichtig.", "ARR ist eine Kennzahl.", "Die Prognose folgt."
    ]
    assert env.get(f"/api/projects/{identifier}/glossary", headers=HEADERS).json() == {
        "entries": {"ARR": "A R R"}
    }
    result = env.put(
        f"/api/projects/{identifier}/glossary",
        headers=HEADERS,
        json={"entries": {"MEDDPICC": "M E D D P I C C", "SaaS": "Säss"}},
    )
    assert result.status_code == 200
    assert production.project_glossary(identifier)["MEDDPICC"] == "M E D D P I C C"
    assert production._manifest(identifier) == items, "Glossary edits must not alter source text"
    assert env.put(
        f"/api/projects/{identifier}/glossary", headers=HEADERS,
        json={"entries": {"": "bad"}}
    ).status_code == 409


def test_job_queue_control_and_cross_project_isolation(env, monkeypatch):
    identifier, another = uuid.uuid4().hex, uuid.uuid4().hex
    prepare_project(identifier)
    prepare_project(another)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    response = env.post(f"/api/projects/{identifier}/jobs", headers=HEADERS)
    assert response.status_code == 201
    created = response.json()
    assert created["status"] == "queued"
    assert created["total"] == 3
    job_id = created["id"]
    assert env.get(f"/api/projects/{another}/jobs/{job_id}", headers=HEADERS).status_code == 404
    assert env.post(f"/api/projects/{identifier}/jobs", headers=HEADERS).status_code == 409
    paused = env.post(
        f"/api/projects/{identifier}/jobs/{job_id}/control",
        headers=HEADERS, json={"action": "pause"},
    )
    assert paused.status_code == 200 and paused.json()["status"] == "paused"
    assert env.put(
        f"/api/projects/{identifier}/glossary", headers=HEADERS,
        json={"entries": {"ARR": "anders"}}
    ).status_code == 409
    resumed = env.post(
        f"/api/projects/{identifier}/jobs/{job_id}/control",
        headers=HEADERS, json={"action": "resume"},
    )
    assert resumed.json()["status"] == "queued"
    cancelled = env.post(
        f"/api/projects/{identifier}/jobs/{job_id}/control",
        headers=HEADERS, json={"action": "cancel"},
    )
    assert cancelled.json()["status"] == "cancelled"
    assert env.get(f"/api/projects/{identifier}/jobs/{job_id}/download", headers=HEADERS).status_code == 404


def test_pcm_wav_health_check(tmp_path):
    path = tmp_path / "short.wav"
    sample_rate = 16000
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        data = [int(7000 * math.sin(2 * math.pi * 220 * i / sample_rate))
                for i in range(sample_rate)]
        out.writeframes(struct.pack("<" + "h" * len(data), *data))
    assert production._valid_audio(path) == pytest.approx(1)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(bytes(sample_rate * 2))
    with pytest.raises(ValueError, match="keine Sprache"):
        production._valid_audio(path)


def test_truncated_wav_is_not_reused_from_checkpoint(tmp_path):
    """A valid header and first second must not hide a truncated tail."""
    path = tmp_path / "truncated.wav"
    rate = 16000
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(struct.pack("<" + "h" * (rate * 2), *([1200] * (rate * 2))))
    with path.open("r+b") as stream:
        stream.truncate(path.stat().st_size - 9000)
    with pytest.raises(ValueError, match="abgeschnitten"):
        production._valid_audio(path)


def test_retry_cannot_invalidate_audio_during_export(env, monkeypatch):
    """A retry must not delete a WAV while an export holds the GPU lock."""
    project_id = uuid.uuid4().hex
    job_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    now = production._now()
    with studio._db() as db:
        db.execute("INSERT INTO studio_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            job_id, project_id, production.VOICE_ID, "generated", 1, 1,
            "Vorwort", 1.0, "", now, now,
        ))
    audio = production._jobs_dir(job_id) / "audio" / "0000000.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"previous verified audio")
    acquired = production._GPU.acquire(blocking=False)
    assert acquired
    try:
        response = env.post(
            f"/api/projects/{project_id}/jobs/{job_id}/segments/0/retry", headers=HEADERS,
        )
        assert response.status_code == 409
        assert audio.read_bytes() == b"previous verified audio"
        assert production._job(job_id)["status"] == "generated"
    finally:
        production._GPU.release()
    retry = env.post(
        f"/api/projects/{project_id}/jobs/{job_id}/segments/0/retry", headers=HEADERS,
    )
    assert retry.status_code == 200
    assert retry.json()["status"] == "queued"
    assert not audio.exists()


def test_cancellation_during_inference_never_marks_project_ready(env, monkeypatch):
    project_id = uuid.uuid4().hex
    job_id = uuid.uuid4().hex
    prepare_project(project_id)
    now = production._now()
    with studio._db() as db:
        db.execute("INSERT INTO studio_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            job_id, project_id, production.VOICE_ID, "running", 0, 1,
            "", 0.0, "", now, now,
        ))
    production._atomic_json(production._jobs_dir(job_id) / "plan.json", {
        "segments": [{"title": "Vorwort", "text": "Eine wichtige Aussage."}],
        "glossary": {},
    })
    monkeypatch.setattr(production, "_normalize_de", lambda text, _: text)
    monkeypatch.setattr(production, "_audio_quality", lambda *_: {"status": "not_checked"})

    def cancel_during_synthesis(_prompt, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"completed segment; real GPU not used")
        production.change_job(project_id, job_id, "cancel")
        return 1.0

    monkeypatch.setattr(production, "_synthesize", cancel_during_synthesis)
    production._STOP.clear()
    try:
        production._run(job_id)
    finally:
        production._STOP.set()
    assert production._job(job_id)["status"] == "cancelled"
    assert studio._project(project_id)["status"] == "Vorbereitet"
    # The completed segment stays cached for an explicit retry/recovery.
    assert (production._jobs_dir(job_id) / "audio" / "0000000.wav").exists()


def test_deletion_rejected_while_gpu_is_processing(env):
    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    assert production._GPU.acquire(blocking=False)
    try:
        denied = env.delete(f"/api/projects/{project_id}", headers=HEADERS)
        assert denied.status_code == 409
        assert studio._project(project_id)["id"] == project_id
    finally:
        production._GPU.release()
    removed = env.delete(f"/api/projects/{project_id}", headers=HEADERS)
    assert removed.status_code == 200
    assert removed.json()["deleted"] is True


def test_simultaneous_requests_cannot_create_two_active_jobs(env, monkeypatch):
    """Both callers can prepare a manifest, but only one can enqueue."""
    from concurrent.futures import ThreadPoolExecutor

    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(production.create_job, project_id) for _ in range(2)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(("created", future.result()))
            except ValueError as exc:
                outcomes.append(("rejected", str(exc)))
    assert sorted(result[0] for result in outcomes) == ["created", "rejected"]
    assert len(production.list_jobs(project_id)) == 1
