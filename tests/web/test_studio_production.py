"""Offline studio regression coverage: credentials, queue state and existing book schema.

No model weights, cloned voices or user-owned books are needed for these tests.
"""
from __future__ import annotations

import errno
import json
import logging
import math
import struct
import time
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


@pytest.mark.parametrize("failure", ["disk-full", "memory", "cuda-oom", "invalid-wav"])
def test_worker_failure_cleans_partial_wav_and_allows_retry(env, monkeypatch, caplog, failure):
    from test_offline_acceptance import write_tone

    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    monkeypatch.setattr(production, "_model_identity", lambda: "e03a-synthetic-worker")
    monkeypatch.setattr(production, "_config", lambda: ("synthetic-python", "synthetic-script"))
    monkeypatch.setenv("AUDIOBOOK_STUDIO_ASR_MODEL", "")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_PERSISTENT_TTS", "1")
    marker = "synthetic private book passage"
    errors = {
        "disk-full": OSError(errno.ENOSPC, marker),
        "memory": MemoryError(marker),
        "cuda-oom": RuntimeError("CUDA out of memory: " + marker),
    }
    attempts = []

    def broken_inference(text, path, config, on_progress=None):
        attempts.append(text)
        path.write_bytes(b"RIFF")
        if failure in errors:
            raise errors[failure]

    monkeypatch.setattr(production, "_generate_with_worker", broken_inference)
    route = f"/api/projects/{project_id}/jobs"
    job = env.post(route, headers=HEADERS).json()
    route += f"/{job['id']}"

    def wait_for(status):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            current = env.get(route, headers=HEADERS).json()
            if current["status"] == status:
                return current
            time.sleep(0.05)
        pytest.fail(f"Job did not reach {status}")

    with caplog.at_level(logging.WARNING, logger="german-audiobook-studio.engine"):
        production.init()
        job = wait_for("failed")
        assert job["done"] == 0
        assert len(attempts) == (3 if failure in {"cuda-oom", "invalid-wav"} else 1)
        assert job["error"] and marker not in job["error"]
        assert marker not in caplog.text
        audio = production._jobs_dir(job["id"]) / "audio"
        assert not list(audio.iterdir())
        assert env.get(route + "/download", headers=HEADERS).status_code == 404
        monkeypatch.setattr(production, "_generate_with_worker",
                            lambda text, path, config, on_progress=None: write_tone(text, path))
        assert env.post(route + "/control", headers=HEADERS, json={"action": "resume"}).is_success
        recovered = wait_for("generated")
        assert recovered["done"] == recovered["total"]
        assert recovered["error"] == ""


def test_private_production_endpoints_fail_closed(env):
    project_id = uuid.uuid4().hex
    assert env.get(f"/api/projects/{project_id}/jobs").status_code == 401
    assert env.get(f"/api/projects/{project_id}/glossary").status_code == 401
    assert env.post(f"/api/projects/{project_id}/jobs").status_code == 401
    assert env.post("/api/voices/preview").status_code == 401
    assert env.post("/api/voices/approve").status_code == 401


def test_legacy_raw_job_errors_are_redacted_on_detail_and_list(env, monkeypatch):
    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    job = production.create_job(project_id)
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='failed',error=? WHERE id=?",
                   ("synthetic private path from an earlier version", job["id"]))
    route = f"/api/projects/{project_id}/jobs"
    assert env.get(f"{route}/{job['id']}", headers=HEADERS).json()["error"] == production.JOB_FAILURE
    assert env.get(route, headers=HEADERS).json()["jobs"][0]["error"] == production.JOB_FAILURE


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


def test_job_eta_uses_real_persisted_processing_times(env, monkeypatch):
    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    created = production.create_job(project_id)
    job_id = created["id"]
    plan_file = production._jobs_dir(job_id) / "plan.json"
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    # Three completed and three outstanding 100-character segments.
    plan["segments"] = [
        {"text": "A" * 100, "title": "Kapitel 1", "chapter_index": 0}
        for _ in range(6)
    ]
    production._atomic_json(plan_file, plan)
    with studio._db() as db:
        db.execute(
            "UPDATE studio_jobs SET status='running',done=3,total=6 WHERE id=?",
            (job_id,),
        )
    audio_dir = production._jobs_dir(job_id) / "audio"
    audio_dir.mkdir(exist_ok=True)
    for index, seconds in enumerate((4.0, 5.0, 6.0)):
        production._atomic_json(audio_dir / f"{index:07d}.json", {
            "prompt": "A" * 100, "inference_seconds": 0.5,
            "processing_seconds": seconds,
        })
    endpoint = f"/api/projects/{project_id}/jobs/{job_id}/activity"
    response = env.get(endpoint, headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["percent"] == 50
    assert data["eta_sample_count"] == 3
    assert data["remaining_seconds"] == 15
    assert data["elapsed_seconds"] >= 0
    assert data["calculated_at"]
    production._atomic_json(audio_dir / "0000002.json", {
        "prompt": "A" * 100, "processing_seconds": 2.0
    })
    revised = env.get(endpoint, headers=HEADERS).json()
    assert revised["remaining_seconds"] == 12

    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='paused' WHERE id=?", (job_id,))
    paused = env.get(endpoint, headers=HEADERS).json()
    assert paused["remaining_seconds"] is None

    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='generated',done=6 WHERE id=?", (job_id,))
    generated = env.get(endpoint, headers=HEADERS).json()
    assert generated["remaining_seconds"] == 0
    assert generated["percent"] == 100


def test_private_job_activity_progress_and_bounded_operational_log(env, monkeypatch):
    project_id, foreign_project = uuid.uuid4().hex, uuid.uuid4().hex
    prepare_project(project_id)
    prepare_project(foreign_project)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    job = production.create_job(project_id)
    job_id = job["id"]
    url = f"/api/projects/{project_id}/jobs/{job_id}/activity"
    assert env.get(url).status_code == 401
    assert env.get(
        f"/api/projects/{foreign_project}/jobs/{job_id}/activity",
        headers=HEADERS,
    ).status_code == 404
    initial = env.get(url, headers=HEADERS).json()
    assert initial["status"] == "queued"
    assert initial["done"] == 0
    assert initial["total"] == 3
    assert initial["percent"] == 0
    assert "Warteschlange" in initial["events"][0]["message"]

    with studio._db() as db:
        db.execute(
            "UPDATE studio_jobs SET status='running',done=2 WHERE id=?",
            (job_id,),
        )
    for number in range(80):
        production._job_event(job_id, f"Testschritt {number}.")
    current = env.get(url, headers=HEADERS).json()
    assert current["done"] == 2
    assert current["percent"] == 67
    assert len(current["events"]) == 60
    assert current["events"][0]["message"] == "Testschritt 20."
    assert current["events"][-1]["message"] == "Testschritt 79."
    # The bounded history is persisted; it must not contain source book text.
    raw = (production._jobs_dir(job_id) / "activity.json").read_text()
    assert "Die Pipeline ist wichtig" not in raw


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


def test_cancelled_job_allows_clean_new_attempt(env, monkeypatch):
    """A real cancellation must not permanently strand an imported book."""
    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)

    first = env.post(f"/api/projects/{project_id}/jobs", headers=HEADERS)
    assert first.status_code == 201
    assert studio._project(project_id)["status"] == "Warteschlange"

    cancelled = env.post(
        f"/api/projects/{project_id}/jobs/{first.json()['id']}/control",
        headers=HEADERS, json={"action": "cancel"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert studio._project(project_id)["status"] == "Vorbereitet"

    second = env.post(f"/api/projects/{project_id}/jobs", headers=HEADERS)
    assert second.status_code == 201
    assert second.json()["id"] != first.json()["id"]
    assert production._job(first.json()["id"])["status"] == "cancelled"


def test_deleted_project_cannot_gain_orphan_job(env, monkeypatch):
    """A removal between preflight and DB insert must be caught in transaction."""
    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)

    def remove_during_preflight(_project_id):
        with studio._db() as db:
            db.execute("DELETE FROM studio_projects WHERE id=?", (project_id,))
        return {}

    monkeypatch.setattr(production, "project_glossary", remove_during_preflight)
    with pytest.raises(ValueError, match="zwischenzeitlich gelöscht"):
        production.create_job(project_id)
    with studio._db() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM studio_jobs WHERE project_id=?", (project_id,)
        ).fetchone()[0] == 0
    jobs_dir = studio.data_dir() / "studio-jobs"
    assert not list(jobs_dir.glob("*/plan.json"))


def test_cancelled_job_does_not_become_generated_when_inference_finishes(env, monkeypatch):
    """In-flight TTS may return after cancellation, but never revive the job."""
    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    created = production.create_job(project_id)
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running' WHERE id=?", (created["id"],))

    monkeypatch.setattr(production, "_normalize_de", lambda text, _: text)
    monkeypatch.setattr(production, "_audio_quality", lambda *_: {"status": "not_checked"})

    def cancel_while_synthesizing(_text, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"simulated WAV for cancellation race")
        production.change_job(project_id, created["id"], "cancel")
        return 1.0

    monkeypatch.setattr(production, "_synthesize", cancel_while_synthesizing)
    production._STOP.clear()
    try:
        production._run(created["id"])
    finally:
        production._STOP.set()
    assert production._job(created["id"])["status"] == "cancelled"
    assert studio._project(project_id)["status"] == "Vorbereitet"


def _prepare_fake_installed_voice(monkeypatch):
    """Small artificial files model identity, not downloaded or trusted weights."""
    root = studio.data_dir()
    model = root / "models" / "CosyVoice"
    model.mkdir(parents=True, exist_ok=True)
    script = model / "infer_thorsten.py"
    script.write_text("print('synthetic test script')", encoding="utf-8")
    (model / ".studio-ready").write_text("test-model-version", encoding="utf-8")
    manifest = root / "models" / "thorsten-model-sha256.txt"
    manifest.write_text("synthetic-private-checksum-manifest", encoding="utf-8")
    python = root / "venvs" / "thorsten" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("fake executable for unit tests", encoding="utf-8")
    monkeypatch.setenv("AUDIOBOOK_THORSTEN_PYTHON", str(python))
    monkeypatch.setenv("AUDIOBOOK_THORSTEN_SCRIPT", str(script))
    sample = root / "voice-samples" / "thorsten.wav"
    sample.parent.mkdir(parents=True, exist_ok=True)
    rate = 16000
    with wave.open(str(sample), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(struct.pack("<" + "h" * rate, *([900] * rate)))
    return manifest, script


def test_late_thorsten_installation_is_detected_without_web_restart(env, monkeypatch):
    # RunPod starts FastAPI before the parallel model download finishes.
    # Installation must not be confused with manually approved narration.
    before = env.get("/api/capabilities", headers=HEADERS).json()
    assert before["voice_installed"] is False
    assert before["voice_approved"] is False
    assert before["voice_preview_available"] is False
    assert "noch nicht vollständig installiert" in before["reason"]

    _prepare_fake_installed_voice(monkeypatch)
    # Test the real late-installation discovery path (without exported startup
    # environment variables, as during the normal RunPod boot).
    monkeypatch.delenv("AUDIOBOOK_THORSTEN_SCRIPT", raising=False)
    monkeypatch.delenv("AUDIOBOOK_THORSTEN_PYTHON", raising=False)
    after = env.get("/api/capabilities", headers=HEADERS).json()
    assert after["voice_installed"] is True
    assert after["voice_approved"] is False
    assert after["voice_preview_available"] is True
    assert after["tts_generation"] is False
    assert "ist installiert" in after["reason"]
    assert "muss installiert" not in after["reason"]

    # Explicit approval changes only the approval state, not the install state.
    production.approve_sample()  # Synthetic test only: no genuine listening.
    approved = env.get("/api/capabilities", headers=HEADERS).json()
    assert approved["voice_installed"] is True
    assert approved["voice_approved"] is True
    assert approved["tts_generation"] is True


def test_voice_approval_is_invalidated_after_model_or_code_change(env, monkeypatch):
    manifest, script = _prepare_fake_installed_voice(monkeypatch)
    assert production.voice_ready() is False
    capabilities = env.get("/api/capabilities", headers=HEADERS).json()
    assert capabilities["voice_preview_available"] is True
    assert capabilities["tts_generation"] is False

    production.approve_sample()  # Simulation: not evidence of a real listening test.
    assert production.voice_ready() is True

    manifest.write_text("altered-weight-checksums", encoding="utf-8")
    assert production.voice_ready() is False
    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    with pytest.raises(ValueError, match="freigegebene deutsche Stimme"):
        production.create_job(project_id)

    manifest.write_text("synthetic-private-checksum-manifest", encoding="utf-8")
    assert production.voice_ready() is True
    script.write_text("print('new model runtime')", encoding="utf-8")
    assert production.voice_ready() is False


def test_previously_planned_job_cannot_mix_changed_voice_weights(env, monkeypatch):
    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    manifest, _ = _prepare_fake_installed_voice(monkeypatch)
    production.approve_sample()
    job = production.create_job(project_id)
    plan = json.loads((production._jobs_dir(job["id"]) / "plan.json").read_text(encoding="utf-8"))
    assert plan["version"] == production.narration_plan.PLAN_VERSION
    assert production.narration_plan.validate_narration_plan(plan)
    assert plan["model_identity"] == production._model_identity()
    assert len(plan["model_identity"]) == 64

    manifest.write_text("different-model-after-job-was-created", encoding="utf-8")
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running' WHERE id=?", (job["id"],))
    with pytest.raises(ValueError, match="Modellgewichte"):
        production._run(job["id"])


def test_quality_report_counts_disabled_and_missing_as_unchecked(env, monkeypatch):
    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    job = production.create_job(project_id)
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='generated',done=3 WHERE id=?", (job["id"],))

    audio_dir = production._jobs_dir(job["id"]) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / "0000000.json").write_text(
        json.dumps({"quality": {"status": "passed", "warnings": [],
                                "asr_wer": 0.0, "asr_cer": 0.0}}),
        encoding="utf-8",
    )
    (audio_dir / "0000001.json").write_text(
        json.dumps({"quality": {"status": "not_checked", "asr_state": "missing",
                                "reason": "ASR ist in dieser Web-Runtime nicht installiert.",
                                "warnings": []}}),
        encoding="utf-8",
    )
    endpoint = f"/api/projects/{project_id}/jobs/{job['id']}/quality"
    assert env.get(endpoint).status_code == 401
    report = env.get(endpoint, headers=HEADERS).json()
    assert report["checked"] == 1
    assert report["unchecked"] == 2
    assert report["total"] == 3
    assert [issue["index"] for issue in report["issues"]] == [1, 2]
    assert report["issues"][0]["status"] == "not_checked"
    assert report["issues"][1]["reason"] == "Kein Segmentprüfbericht vorhanden."
    (audio_dir / "0000002.json").write_text("null", encoding="utf-8")
    report = env.get(endpoint, headers=HEADERS).json()
    assert report["unchecked"] == 2
    assert report["issues"][1]["reason"] == "Segmentprüfbericht beschädigt."


def test_asr_capabilities_are_authenticated_and_explicit(env, monkeypatch):
    monkeypatch.setenv("AUDIOBOOK_STUDIO_ASR_MODEL", "off")
    assert env.get("/api/capabilities").status_code == 401
    result = env.get("/api/capabilities", headers=HEADERS).json()
    assert result["asr"]["state"] == "disabled"
    assert result["asr"]["enabled"] is False
