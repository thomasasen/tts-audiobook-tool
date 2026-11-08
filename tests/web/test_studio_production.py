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
    # Legacy regression cases intentionally exercise serial operation.
    # The production default of two workers has a separate new-job test.
    monkeypatch.setenv("AUDIOBOOK_STUDIO_TTS_WORKERS", "1")
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
    initial = env.get(f"/api/projects/{identifier}/glossary", headers=HEADERS).json()
    assert initial["entries"] == {"ARR": "A R R"}
    assert initial["update"]["status"] == "idle"
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
    edited = env.put(
        f"/api/projects/{identifier}/glossary", headers=HEADERS,
        json={"entries": {"ARR": "anders"}}
    )
    assert edited.status_code == 200
    assert edited.json()["update"]["status"] == "pending"
    assert env.get(
        f"/api/projects/{identifier}/glossary", headers=HEADERS
    ).json()["entries"] == {"ARR": "anders"}
    assert production._process_pending_glossary()
    result = env.get(
        f"/api/projects/{identifier}/glossary/update", headers=HEADERS
    ).json()
    assert result["status"] == "applied"
    assert result["affected"] == 1
    assert production._job(job_id)["status"] == "paused"
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


def test_studio_recognizes_actual_pinned_thorsten_marker_after_install(env):
    """Regression: installer writes 289-byte marker; the web server starts first.

    No real GPU or model weights are needed: this checks only the lightweight
    installed-voice identity handshake, not model inference or integrity.
    """
    root = studio.data_dir()
    before = env.get("/api/capabilities", headers=HEADERS).json()
    assert before["voice_installed"] is False

    pins = (Path(__file__).resolve().parents[2] / "runpod" / "model-pins.env").read_text(
        encoding="utf-8"
    )
    values = dict(
        line.split("=", 1) for line in pins.splitlines()
        if line.startswith(("MODEL_RELEASE_ID=", "THORSTEN_PYTHON_VERSION=", "UV_VERSION="))
    )
    # Construct exactly the same format as runpod/install-thorsten.sh. The
    # lock digest is a fixed synthetic hash, never a fake validated model.
    version = (
        values["MODEL_RELEASE_ID"].strip('"')
        + "-deps-" + "a" * 64
        + "-py-" + values["THORSTEN_PYTHON_VERSION"].strip('"')
        + "-uv-" + values["UV_VERSION"].strip('"')
    )
    assert len((version + "\n").encode("utf-8")) == 289

    model = root / "models" / "CosyVoice"
    model.mkdir(parents=True)
    (model / ".studio-ready").write_text(version + "\n", encoding="utf-8")
    (model / "infer_thorsten.py").write_text("print('synthetic inference')", encoding="utf-8")
    (root / "models" / "thorsten-model-sha256.txt").write_text(
        "synthetic-model-manifest-for-test", encoding="utf-8"
    )
    python = root / "venvs" / "thorsten" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("synthetic runtime placeholder", encoding="utf-8")

    # No process restart; next authenticated request must see installation.
    result = env.get("/api/capabilities", headers=HEADERS)
    assert result.status_code == 200
    capabilities = result.json()
    assert capabilities["voice_installed"] is True
    assert capabilities["voice_preview_available"] is True
    assert capabilities["voice_approved"] is False
    assert capabilities["voice_install_state"] == "installed"
    assert "Hörprobe" in capabilities["reason"]
    assert production._model_identity() is not None

    # Still reject unexpectedly oversized markers rather than removing bounds.
    (model / ".studio-ready").write_text("x" * 513, encoding="utf-8")
    oversized = env.get("/api/capabilities", headers=HEADERS).json()
    assert oversized["voice_installed"] is False
    assert oversized["voice_preview_available"] is False


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


def test_asr_capabilities_are_authenticated_and_explicit(env, monkeypatch):
    monkeypatch.setenv("AUDIOBOOK_STUDIO_ASR_MODEL", "off")
    assert env.get("/api/capabilities").status_code == 401
    result = env.get("/api/capabilities", headers=HEADERS).json()
    assert result["asr"]["state"] == "disabled"
    assert result["asr"]["enabled"] is False


def test_verified_voice_settings_are_private_persistent_and_project_scoped(env):
    first, second = uuid.uuid4().hex, uuid.uuid4().hex
    prepare_project(first)
    prepare_project(second)
    endpoint = f"/api/projects/{first}/voice-settings"
    assert env.get(endpoint).status_code == 401
    assert env.put(endpoint, json={"speed": 1.05, "preview_text": "Probe."}).status_code == 401
    assert env.get(endpoint, headers=HEADERS).json() == {
        "speed": 1.0, "preview_text": "",
    }
    data = {"speed": 1.05, "preview_text": "ARR steigt in diesem Geschäftsjahr deutlich."}
    saved = env.put(endpoint, headers=HEADERS, json=data)
    assert saved.status_code == 200, saved.text
    assert saved.json() == data
    assert env.get(endpoint, headers=HEADERS).json() == data
    assert env.get(f"/api/projects/{second}/voice-settings", headers=HEADERS).json()["speed"] == 1.0
    assert (studio._project_dir(first) / "voice-settings.json").is_file()
    for bad in (
        {"speed": 1.17, "preview_text": ""},
        {"speed": 0.4, "preview_text": ""},
        {"speed": True, "preview_text": ""},
        {"speed": 1.0, "preview_text": "x"},
    ):
        result = env.put(endpoint, headers=HEADERS, json=bad)
        assert result.status_code in (409, 422)
    assert env.get(endpoint, headers=HEADERS).json() == data


def test_voice_settings_blocked_while_job_resumable(env):
    identifier = uuid.uuid4().hex
    prepare_project(identifier)
    now = production._now()
    with studio._db() as db:
        db.execute("INSERT INTO studio_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            uuid.uuid4().hex, identifier, production.VOICE_ID, "paused",
            0, 1, "", 0.0, "", now, now,
        ))
    response = env.put(
        f"/api/projects/{identifier}/voice-settings", headers=HEADERS,
        json={"speed": 1.1, "preview_text": ""},
    )
    assert response.status_code == 409
    assert "gesperrt" in response.json()["detail"]


def test_verified_voice_speed_stored_in_immutable_plan(env, monkeypatch):
    identifier = uuid.uuid4().hex
    prepare_project(identifier)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    data = {"speed": 1.1, "preview_text": "Dieser Text ist nur für die Hörprobe."}
    result = env.put(
        f"/api/projects/{identifier}/voice-settings", headers=HEADERS,
        json=data,
    )
    assert result.status_code == 200, result.text
    job = production.create_job(identifier)
    plan = json.loads(
        (production._jobs_dir(job["id"]) / "plan.json").read_text(encoding="utf-8")
    )
    assert plan["voice_settings"] == {"speed": 1.1}
    assert "preview_text" not in json.dumps(plan, ensure_ascii=False)
    assert production.narration_plan.validate_narration_plan(plan) is True
    original = plan["config_fingerprint"]
    altered = json.loads(json.dumps(plan))
    altered["voice_settings"]["speed"] = 1.0
    with pytest.raises(ValueError, match="Fingerprint"):
        production.narration_plan.validate_narration_plan(altered)
    assert original != production.narration_plan._digest({
        "voice": plan["voice"],
        "model_identity": plan["model_identity"],
        "audio": plan["audio"],
        "glossary": plan["glossary"],
        "normalizer_version": plan["normalizer_version"],
        "voice_settings": {"speed": 1.0},
    })


def test_custom_preview_text_and_speed_reach_real_synthesis_adapter(env, monkeypatch):
    identifier = uuid.uuid4().hex
    prepare_project(identifier)
    monkeypatch.setattr(production, "_model_identity", lambda: "synthetic-installed-model")
    seen = []

    def fake_synthesize(text, path, on_progress=None, speed=1.0):
        seen.append((text, speed))
        if on_progress:
            on_progress("Test-Audio wird erzeugt.")
        path.parent.mkdir(parents=True, exist_ok=True)
        rate = 16000
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(struct.pack("<" + "h" * rate, *([700] * rate)))
        return 1.0

    monkeypatch.setattr(production, "_synthesize", fake_synthesize)
    path = production.make_sample(
        duration_seconds=5, speed=1.05,
        preview_text="ARR hat in diesem Jahr einen hohen Wert erzielt.",
        project_id=identifier,
    )
    assert path.is_file()
    assert production._valid_browser_preview(path) == pytest.approx(1.0)
    assert len(seen) == 1 and seen[0][1] == 1.05
    assert "A R R" in seen[0][0]
    assert "hohen Wert" in seen[0][0]
    assert "helpful assistant" not in seen[0][0].lower()


def test_historical_plan_without_voice_speed_remains_valid(env, monkeypatch):
    identifier = uuid.uuid4().hex
    prepare_project(identifier)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    job = production.create_job(identifier)
    plan = json.loads(
        (production._jobs_dir(job["id"]) / "plan.json").read_text(encoding="utf-8")
    )
    plan.pop("voice_settings")
    plan["config_fingerprint"] = production.narration_plan._digest({
        "voice": plan["voice"], "model_identity": plan["model_identity"],
        "audio": plan["audio"], "glossary": plan["glossary"],
        "normalizer_version": plan["normalizer_version"],
    })
    plan["plan_fingerprint"] = production.narration_plan._digest({
        "schema": plan["schema"], "project_id": plan["project_id"],
        "book_fingerprint": plan["book_fingerprint"],
        "content_fingerprint": plan["content_fingerprint"],
        "config_fingerprint": plan["config_fingerprint"],
    })
    assert production.narration_plan.validate_narration_plan(plan) is True
    assert production.verified_speed(plan.get("voice_settings", {"speed": 1.0})["speed"]) == 1.0


def test_nondefault_speed_never_uses_unverified_pinned_cli_flag(
    env, monkeypatch, tmp_path,
):
    """Pinned Thorsten infer_thorsten.py has no --speed (Python API does)."""
    from unittest.mock import Mock

    cli = tmp_path / "infer_thorsten.py"
    cli.write_text("# no --speed argument", encoding="utf-8")
    monkeypatch.setattr(production, "_config", lambda: ("/usr/bin/python3", str(cli)))
    monkeypatch.setenv("AUDIOBOOK_STUDIO_PERSISTENT_TTS", "0")
    forbidden = Mock(side_effect=AssertionError("Old CLI must not execute with --speed"))
    monkeypatch.setattr(production.subprocess, "run", forbidden)
    with pytest.raises(RuntimeError, match="persistenten Thorsten-Worker"):
        production._synthesize("Ein Testsatz.", tmp_path / "sample.wav", speed=1.05)
    forbidden.assert_not_called()


def test_chapters_publish_incrementally_as_authenticated_m4a(env, monkeypatch):
    """Real PyAV audio encoder, fake GPU speech, live API between chapters."""
    import io
    import av

    project_id = uuid.uuid4().hex
    other_project = uuid.uuid4().hex
    prepare_project(project_id)
    prepare_project(other_project)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    created = production.create_job(project_id)
    job_id = created["id"]
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running' WHERE id=?", (job_id,))
    endpoint = f"/api/projects/{project_id}/jobs/{job_id}/chapters"
    assert env.get(endpoint).status_code == 401
    assert env.get(endpoint, headers=HEADERS).json()["chapters"] == []
    assert env.get(
        f"/api/projects/{other_project}/jobs/{job_id}/chapters", headers=HEADERS,
    ).status_code == 404

    monkeypatch.setattr(production, "_audio_quality", lambda *_: {"status": "not_checked"})
    calls = []

    def fake_synthesis(text, path):
        calls.append(text)
        # First two segments make Vorwort, the third starts Kapitel 1.
        if len(calls) == 3:
            response = env.get(endpoint, headers=HEADERS)
            assert response.status_code == 200
            ready = response.json()["chapters"]
            assert len(ready) == 1
            assert ready[0]["title"] == "Vorwort"
            assert ready[0]["segment_count"] == 2
            assert ready[0]["duration_seconds"] == pytest.approx(2, abs=.12)
            assert ready[0]["bytes"] > 1024
            assert ready[0]["preview_seconds"] == pytest.approx(2, abs=.12)
            media = env.get(
                f"/api/projects/{project_id}/jobs/{job_id}/chapters/0/audio",
                headers=HEADERS,
            )
            assert media.status_code == 200
            assert media.headers["content-type"].startswith("audio/wav")
            assert "no-store" in media.headers["cache-control"]
            with wave.open(io.BytesIO(media.content), "rb") as audition:
                assert audition.getsampwidth() == 2
                assert audition.getnchannels() == 1
                assert audition.getnframes() / audition.getframerate() == pytest.approx(2)
            download = env.get(
                f"/api/projects/{project_id}/jobs/{job_id}/chapters/0/download",
                headers=HEADERS,
            )
            assert download.status_code == 200
            assert download.content != media.content
            assert b"ftyp" in download.content[:80]
            with av.open(io.BytesIO(download.content), format="mp4") as container:
                assert len(container.streams.audio) == 1
                decoded = list(container.decode(audio=0))
                assert len(decoded) > 0
            assert "Kapitel-001.m4a" in download.headers["content-disposition"]
            assert env.get(
                f"/api/projects/{project_id}/jobs/{job_id}/chapters/1/audio",
                headers=HEADERS,
            ).status_code == 404
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(struct.pack("<h", 1200) * 16000)
        return 1.0

    monkeypatch.setattr(production, "_synthesize", fake_synthesis)
    production._STOP.clear()
    try:
        production._run(job_id)
    finally:
        production._STOP.set()
    assert len(calls) == 3
    assert production._job(job_id)["status"] == "generated"
    chapters = env.get(endpoint, headers=HEADERS).json()["chapters"]
    assert [row["title"] for row in chapters] == ["Vorwort", "Kapitel 1"]
    assert [row["index"] for row in chapters] == [0, 1]
    assert chapters[1]["duration_seconds"] == pytest.approx(1, abs=.12)
    # Metadata and output persist without regenerating from model after restart.
    assert production._ready_chapter_audio(job_id, production._chapter_index(job_id)[0])
    assert env.get(endpoint, headers=HEADERS).json()["chapters"] == chapters
    assert (production._jobs_dir(job_id) / "chapters" / "00000.m4a").is_file()
    assert (production._jobs_dir(job_id) / "chapters" / "00001.m4a").is_file()


def test_incomplete_stale_or_wrong_project_chapter_is_not_downloadable(env, monkeypatch):
    project_id, other = uuid.uuid4().hex, uuid.uuid4().hex
    prepare_project(project_id)
    prepare_project(other)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    job_id = production.create_job(project_id)["id"]
    base = f"/api/projects/{project_id}/jobs/{job_id}/chapters"
    index = production._chapter_index(job_id)
    assert len(index) == 2
    path, meta = production._chapter_audio_paths(job_id, 0)
    path.write_bytes(b"x" * 2048)
    audition = production._chapter_preview_path(job_id, 0)
    audition.write_bytes(b"w" * 4096)
    production._atomic_json(meta, {
        "version": production.CHAPTER_AUDIO_CACHE_VERSION,
        "fingerprint": index[0]["fingerprint"], "start": 0, "end": 2,
        "duration": 12.0, "byte_size": 2048,
        "preview_duration": 4.0, "preview_byte_size": 4096,
    })
    # Matching metadata alone must never expose unfinished audio.
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running', done=1 WHERE id=?", (job_id,))
    assert env.get(base, headers=HEADERS).json()["chapters"] == []
    assert env.get(base + "/0/audio", headers=HEADERS).status_code == 404
    assert env.get(base + "/0/download", headers=HEADERS).status_code == 404
    assert env.get(
        f"/api/projects/{other}/jobs/{job_id}/chapters/0/audio", headers=HEADERS,
    ).status_code == 404
    assert env.get(base + "/-1/audio", headers=HEADERS).status_code == 404
    assert env.get(base + "/30/audio", headers=HEADERS).status_code == 404
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET done=2 WHERE id=?", (job_id,))
    assert len(env.get(base, headers=HEADERS).json()["chapters"]) == 1
    production._atomic_json(meta, {
        "version": production.CHAPTER_AUDIO_CACHE_VERSION,
        "fingerprint": "invalid", "start": 0, "end": 2,
        "duration": 12.0, "byte_size": 2048,
        "preview_duration": 4.0, "preview_byte_size": 4096,
    })
    assert env.get(base, headers=HEADERS).json()["chapters"] == []
    assert env.get(base + "/0/download", headers=HEADERS).status_code == 404


def test_retry_invalidates_only_affected_chapter_cache(env, monkeypatch):
    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    job_id = production.create_job(project_id)["id"]
    idx = production._chapter_index(job_id)
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='generated', done=3 WHERE id=?", (job_id,))
    for chapter in idx:
        path, meta = production._chapter_audio_paths(job_id, chapter["index"])
        path.write_bytes(b"m4a" * 1000)
        preview = production._chapter_preview_path(job_id, chapter["index"])
        preview.write_bytes(b"wav" * 1000)
        production._atomic_json(meta, {
            "version": production.CHAPTER_AUDIO_CACHE_VERSION,
            "fingerprint": chapter["fingerprint"],
            "start": chapter["start"], "end": chapter["end"],
            "duration": 15.0, "byte_size": path.stat().st_size,
            "preview_duration": 9.0, "preview_byte_size": preview.stat().st_size,
        })
    original = production._jobs_dir(job_id) / "audio" / "0000000.wav"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"cached segment")
    production.retry_segment(project_id, job_id, 0)
    first, _ = production._chapter_audio_paths(job_id, 0)
    second, _ = production._chapter_audio_paths(job_id, 1)
    assert not first.exists()
    assert not production._chapter_preview_path(job_id, 0).exists()
    assert second.is_file()
    assert production._chapter_preview_path(job_id, 1).is_file()
    # Progress is reset pending rescan; do not expose even retained chapters.
    base = f"/api/projects/{project_id}/jobs/{job_id}/chapters"
    assert env.get(base, headers=HEADERS).json()["chapters"] == []


def test_duplicate_chapter_titles_keep_separate_source_identity():
    from tts_audiobook_tool.web_studio import narration_plan
    plan = {
        "segments": [
            {"chapter": 0, "title": "Einleitung", "text": "A"},
            {"chapter": 0, "title": "Einleitung", "text": "B"},
            {"chapter": 1, "title": "Einleitung", "text": "C"},
            {"chapter": 2, "title": "Kapitel", "text": "D"},
        ],
        "glossary": {}, "model_identity": "", "normalizer_version": None,
    }
    groups = production._chapter_ranges(plan)
    assert [(item["start"], item["end"], item["title"]) for item in groups] == [
        (0, 2, "Einleitung"), (2, 3, "Einleitung"), (3, 4, "Kapitel"),
    ]
    assert len({item["fingerprint"] for item in groups}) == 3


def _glossary_mock_audio(text: str, path: Path) -> float:
    """Deterministic actual PCM16 for checkpoint tests; no GPU or personal EPUB."""
    rate = 16000
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(struct.pack("<h", 1500) * rate)
    return 1.0


def test_incremental_glossary_keeps_unaffected_segments_and_chapters(env, monkeypatch):
    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    monkeypatch.setattr(production, "_audio_quality", lambda *_: {"status": "not_checked"})
    calls = []
    def synth(text, path):
        calls.append(text)
        return _glossary_mock_audio(text, path)
    monkeypatch.setattr(production, "_synthesize", synth)
    job_id = production.create_job(project_id)["id"]
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running' WHERE id=?", (job_id,))
    production._STOP.clear()
    try:
        production._run(job_id)
    finally:
        production._STOP.set()
    assert production._job(job_id)["status"] == "generated"
    assert len(calls) == 3
    root = production._jobs_dir(job_id)
    before_audio = [(root / "audio" / f"{i:07d}.wav").read_bytes() for i in range(3)]
    before_meta = [
        json.loads((root / "audio" / f"{i:07d}.json").read_text(encoding="utf-8"))
        for i in range(3)
    ]
    before_chapters = production._chapter_index(job_id)
    assert production._ready_chapter_audio(job_id, before_chapters[0]) is not None
    assert production._ready_chapter_audio(job_id, before_chapters[1]) is not None
    update = env.put(
        f"/api/projects/{project_id}/glossary", headers=HEADERS,
        json={"entries": {"ARR": "A A R"}} ,
    )
    assert update.status_code == 200
    assert update.json()["update"]["status"] == "pending"
    # A second edit is blocked until the first change is committed.
    second = env.put(
        f"/api/projects/{project_id}/glossary", headers=HEADERS,
        json={"entries": {"ARR": "Aaa"}},
    )
    assert second.status_code == 409
    assert production._job(job_id)["status"] == "paused"
    assert production._process_pending_glossary()
    result = env.get(f"/api/projects/{project_id}/glossary/update", headers=HEADERS).json()
    assert result["status"] == "applied"
    assert result["affected"] == 1
    assert result["preserved"] == 2
    assert result["total"] == 3
    assert production._job(job_id)["status"] == "queued"
    after_chapters = production._chapter_index(job_id)
    assert production._ready_chapter_audio(job_id, after_chapters[0]) is None
    assert production._ready_chapter_audio(job_id, after_chapters[1]) is not None
    assert not (root / "audio" / "0000001.wav").exists()
    assert not (root / "audio" / "0000001.json").exists()
    for i in (0, 2):
        assert (root / "audio" / f"{i:07d}.wav").read_bytes() == before_audio[i]
        new_meta = json.loads((root / "audio" / f"{i:07d}.json").read_text())
        assert new_meta["audio_sha256"] == before_meta[i]["audio_sha256"]
        assert new_meta["fingerprint"] != before_meta[i]["fingerprint"]

    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running' WHERE id=?", (job_id,))
    production._STOP.clear()
    try:
        production._run(job_id)
    finally:
        production._STOP.set()
    assert len(calls) == 4, "Only the genuinely changed spoken segment may be resynthesized"
    assert "A A R" in calls[-1]
    assert production._job(job_id)["status"] == "generated"
    assert all(production._ready_chapter_audio(job_id, item) for item in after_chapters)


def test_glossary_edit_during_gpu_inference_pauses_at_segment_boundary(env, monkeypatch):
    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    monkeypatch.setattr(production, "_audio_quality", lambda *_: {"status": "not_checked"})
    job_id = production.create_job(project_id)["id"]
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running' WHERE id=?", (job_id,))
    calls = []
    def synth(text, path):
        calls.append(text)
        if len(calls) == 1:
            response = env.put(
                f"/api/projects/{project_id}/glossary", headers=HEADERS,
                json={"entries": {"ARR": "A A R"}},
            )
            assert response.status_code == 200
            assert response.json()["update"]["status"] == "pending"
            assert production._job(job_id)["status"] == "paused"
        return _glossary_mock_audio(text, path)
    monkeypatch.setattr(production, "_synthesize", synth)
    production._STOP.clear()
    try:
        production._run(job_id)
    finally:
        production._STOP.set()
    assert len(calls) == 1
    assert (production._jobs_dir(job_id) / "audio" / "0000000.json").is_file()
    assert production._job(job_id)["status"] == "paused"
    assert production._process_pending_glossary()
    assert production._job(job_id)["status"] == "queued"
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running' WHERE id=?", (job_id,))
    production._STOP.clear()
    try:
        production._run(job_id)
    finally:
        production._STOP.set()
    # The originally completed segment was retained; two remaining segments
    # include exactly one with a new pronunciation.
    assert len(calls) == 3
    assert production._job(job_id)["status"] == "generated"


def test_glossary_rule_not_used_causes_no_audio_regeneration(env, monkeypatch):
    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    job_id = production.create_job(project_id)["id"]
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='generated',done=3 WHERE id=?", (job_id,))
    result = env.put(
        f"/api/projects/{project_id}/glossary", headers=HEADERS,
        json={"entries": {"ARR": "A R R", "Begriff ohne Vorkommen": "neue Form"}},
    )
    assert result.status_code == 200
    assert production._process_pending_glossary()
    status = env.get(f"/api/projects/{project_id}/glossary/update", headers=HEADERS).json()
    assert status["affected"] == 0
    assert production._job(job_id)["status"] == "generated"
    assert production.project_glossary(project_id)["Begriff ohne Vorkommen"] == "neue Form"


def test_glossary_revision_survives_interruption_mid_migration(env, monkeypatch):
    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    job_id = production.create_job(project_id)["id"]
    assert env.put(
        f"/api/projects/{project_id}/glossary", headers=HEADERS,
        json={"entries": {"ARR": "A A R"}},
    ).status_code == 200
    orig = production._atomic_json
    fail_once = [True]
    def crash_on_project_glossary(path, data):
        if (Path(path).name == "project.json"
                and fail_once[0]):
            fail_once[0] = False
            raise OSError("simulated process interruption before project commit")
        return orig(path, data)
    monkeypatch.setattr(production, "_atomic_json", crash_on_project_glossary)
    assert production._process_pending_glossary()
    failed = production.glossary_update_status(project_id)
    assert failed["status"] == "failed"
    assert production._job(job_id)["status"] == "paused"
    assert production._glossary_pending_path(project_id).is_file()
    monkeypatch.setattr(production, "_atomic_json", orig)
    production._GLOSSARY_APPLY_FAILED.clear()  # equivalent to recovery initialization
    assert production._process_pending_glossary()
    applied = production.glossary_update_status(project_id)
    assert applied["status"] == "applied"
    assert applied["affected"] == 1
    assert not production._glossary_pending_path(project_id).exists()
    assert production._job(job_id)["status"] == "queued"
    assert production.project_glossary(project_id)["ARR"] == "A A R"


def test_opt_in_parallel_commits_ordered_wavs_and_reuses_checkpoints(env, monkeypatch, tmp_path):
    """Actual NarrationPlan, fake concurrent pool; no CUDA or licensed text."""
    from concurrent.futures import Future

    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_TTS_WORKERS", "2")
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    monkeypatch.setattr(production, "_config", lambda: ("fake-python", "fake-infer"))
    monkeypatch.setattr(production, "_audio_quality", lambda *_: {"status": "not_checked"})
    monkeypatch.setattr(production, "_assemble_chapter_audio", lambda *_: False)

    calls = []
    class FakePool:
        count = 2
        def __init__(self, config, count, data_root):
            assert count == 2
            self.closed = False
        def submit(self, text, partial, speed):
            assert partial.name.endswith(".partial.wav")
            assert speed == 1.0
            calls.append(text)
            _glossary_mock_audio(text, partial)
            ready = Future()
            ready.set_result(0.125)
            return ready
        def close(self):
            self.closed = True

    monkeypatch.setattr(production, "ParallelWorkerPool", FakePool)
    job = production.create_job(project_id)
    job_id = job["id"]
    plan_path = production._jobs_dir(job_id) / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["parallel_worker_count"] == 2
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running' WHERE id=?", (job_id,))
    production._STOP.clear()
    try:
        production._run(job_id)
    finally:
        production._STOP.set()
    assert production._job(job_id)["status"] == "generated"
    assert len(calls) == 3
    root = production._jobs_dir(job_id) / "audio"
    assert not list(root.glob("*.partial.wav"))
    for index, segment in enumerate(plan["segments"]):
        wav = root / f"{index:07d}.wav"
        meta = json.loads(wav.with_suffix(".json").read_text(encoding="utf-8"))
        assert production._valid_audio(wav) == pytest.approx(1.0)
        assert meta["audio_sha256"] == production._audio_digest(wav)
        assert meta["unit_id"] == segment["unit_id"]
        assert meta["prompt"] == segment["speech_text"]
        assert meta["inference_seconds"] == pytest.approx(0.125, abs=.01)

    # Resume/re-run must not resynthesize matching frozen-plan checkpoints.
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running' WHERE id=?", (job_id,))
    production._STOP.clear()
    try:
        production._run(job_id)
    finally:
        production._STOP.set()
    assert len(calls) == 3
    assert production._job(job_id)["status"] == "generated"


def test_parallel_pause_keeps_only_committed_segments(env, monkeypatch):
    from concurrent.futures import Future

    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setenv("AUDIOBOOK_STUDIO_TTS_WORKERS", "2")
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    monkeypatch.setattr(production, "_config", lambda: ("fake-python", "fake-infer"))
    monkeypatch.setattr(production, "_audio_quality", lambda *_: {"status": "not_checked"})
    monkeypatch.setattr(production, "_assemble_chapter_audio", lambda *_: False)
    job_id = production.create_job(project_id)["id"]
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running' WHERE id=?", (job_id,))

    class PausingPool:
        count = 2
        def __init__(self, *_):
            self.calls = 0
        def submit(self, text, partial, speed):
            self.calls += 1
            _glossary_mock_audio(text, partial)
            ready = Future()
            ready.set_result(0.2)
            if self.calls == 1:
                production.change_job(project_id, job_id, "pause")
            return ready
        def close(self):
            pass

    monkeypatch.setattr(production, "ParallelWorkerPool", PausingPool)
    production._STOP.clear()
    try:
        production._run(job_id)
    finally:
        production._STOP.set()
    assert production._job(job_id)["status"] == "paused"
    root = production._jobs_dir(job_id) / "audio"
    assert (root / "0000000.wav").is_file()
    assert (root / "0000000.json").is_file()
    assert not (root / "0000001.wav").exists()
    assert not list(root.glob("*.partial.wav"))


def test_default_new_job_is_frozen_at_two_workers(env, monkeypatch):
    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    monkeypatch.delenv("AUDIOBOOK_STUDIO_TTS_WORKERS")
    job_id = production.create_job(project_id)["id"]
    plan = json.loads((production._jobs_dir(job_id) / "plan.json").read_text())
    assert plan["parallel_worker_count"] == 2


def test_existing_plan_stays_serial_after_parallel_toggle(env, monkeypatch):
    project_id = uuid.uuid4().hex
    prepare_project(project_id)
    monkeypatch.setattr(production, "voice_ready", lambda: True)
    job_id = production.create_job(project_id)["id"]
    plan = json.loads((production._jobs_dir(job_id) / "plan.json").read_text())
    assert plan["parallel_worker_count"] == 1
    monkeypatch.setenv("AUDIOBOOK_STUDIO_TTS_WORKERS", "4")
    monkeypatch.setattr(production, "_audio_quality", lambda *_: {"status": "not_checked"})
    monkeypatch.setattr(production, "_assemble_chapter_audio", lambda *_: False)
    monkeypatch.setattr(production, "ParallelWorkerPool",
                        lambda *_: pytest.fail("Existing serial jobs must not create worker pools"))
    monkeypatch.setattr(production, "_synthesize", _glossary_mock_audio)
    with studio._db() as db:
        db.execute("UPDATE studio_jobs SET status='running' WHERE id=?", (job_id,))
    production._STOP.clear()
    try:
        production._run(job_id)
    finally:
        production._STOP.set()
    assert production._job(job_id)["status"] == "generated"
