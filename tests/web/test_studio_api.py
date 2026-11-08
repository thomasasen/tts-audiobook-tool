"""Web Studio tests: authentication, safe archive handling and German chapter defaults.

These tests intentionally use no downloaded books, models or reference voices.
"""
from __future__ import annotations

import io
import json
import os
import stat
import struct
import threading
import time
import wave
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


def test_csp_allows_local_blob_audio_but_blocks_external_sources(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "media-src 'self' blob:" in csp
    assert "connect-src 'self' blob:" in csp
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert "https:" not in csp
    assert "data:" not in csp.split("media-src", 1)[1].split(";", 1)[0]


def test_health_public_but_projects_private(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/voices").status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/session", headers=authorized()).json()["language"] == "de"


def test_voice_preview_runtime_failure_returns_safe_json(client, monkeypatch):
    from tts_audiobook_tool.web_studio import production

    def fail_preview():
        raise RuntimeError("private worker exception: internal diagnostic only")

    monkeypatch.setattr(production, "make_sample", fail_preview)
    response = client.post("/api/voices/preview", headers=authorized())
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert "Thorsten-Hörprobe konnte nicht erzeugt" in response.json()["detail"]
    assert "private worker exception" not in response.text


def test_broken_cuda_host_reports_actionable_web_error(client, monkeypatch):
    from tts_audiobook_tool.web_studio import production

    def failed_gpu():
        raise RuntimeError(
            "GPU nicht für CUDA-Ausführung verfügbar. "
            "RunPod-Host/GPU-Zuweisung prüfen; runpod/gpu_preflight.py --env both ausführen."
        )

    monkeypatch.setattr(production, "make_sample", failed_gpu)
    response = client.post("/api/voices/preview", headers=authorized())
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    message = response.json()["detail"]
    assert "GPU ist für CUDA nicht betriebsbereit" in message
    assert "runpod/gpu_preflight.py --env both" in message


def _test_preview_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(struct.pack("<" + "h" * 16000, *([500] * 16000)))


def test_browser_preview_accepts_pcm16_and_rejects_24_bit_wav(tmp_path):
    from tts_audiobook_tool.web_studio import production
    candidate = tmp_path / "preview.wav"
    _test_preview_wav(candidate)
    assert production._valid_browser_preview(candidate) == pytest.approx(1.0)
    # A perfectly valid internal PCM-24 WAV must not be advertised as a
    # browser-ready sample. It must be regenerated as signed PCM-16.
    with wave.open(str(candidate), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(3)
        out.setframerate(16000)
        out.writeframes((b"\x00\x80\x00") * 16000)
    assert production._valid_audio(candidate) == pytest.approx(1.0)
    with pytest.raises(ValueError, match="16-Bit-PCM"):
        production._valid_browser_preview(candidate)


def _await_preview_status(client, expected: str) -> dict:
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        result = client.get("/api/voices/preview/status", headers=authorized())
        assert result.status_code == 200
        state = result.json()
        if state["status"] == expected:
            return state
        threading.Event().wait(0.02)
    raise AssertionError("Background preview did not reach status: " + expected)


def test_preview_progress_activity_is_authenticated_and_duplicate_safe(client, monkeypatch):
    from tts_audiobook_tool.web_studio import production, app as studio
    monkeypatch.setattr(production, "_model_identity", lambda: "test-installed-voice")
    entered = threading.Event()
    release = threading.Event()
    seen = []

    def fake_generate(on_progress=None, duration_seconds=5):
        assert duration_seconds == 5
        assert on_progress is not None
        on_progress("Thorsten-Modell wird in den GPU-Speicher geladen.")
        entered.set()
        assert release.wait(4), "Preview test thread was not released"
        path = studio.data_dir() / "voice-samples" / "thorsten.wav"
        _test_preview_wav(path)
        seen.append(True)
        return path

    monkeypatch.setattr(production, "make_sample", fake_generate)
    assert client.get("/api/voices/preview/status").status_code == 401
    assert client.post("/api/voices/preview/start").status_code == 401
    assert client.get("/api/voices/preview/audio/" + "a" * 32).status_code == 401
    try:
        response = client.post("/api/voices/preview/start", headers=authorized())
        assert response.status_code == 202
        task = response.json()
        assert len(task["id"]) == 32
        assert entered.wait(2)
        current = client.get("/api/voices/preview/status", headers=authorized()).json()
        assert current["status"] == "running"
        assert any("Modell" in entry["message"] for entry in current["events"])
        assert client.post("/api/voices/preview/start", headers=authorized()).status_code == 409
        assert client.get("/api/voices/preview/audio/" + task["id"], headers=authorized()).status_code == 404
    finally:
        release.set()
    done = _await_preview_status(client, "ready")
    assert done["error"] == ""
    assert seen == [True]
    assert any("Audiodatei geprüft" in entry["message"] for entry in done["events"])
    file = client.get("/api/voices/preview/audio/" + task["id"], headers=authorized())
    assert file.status_code == 200
    assert file.headers["content-type"].startswith("audio/wav")
    assert len(file.content) > 1000
    assert file.headers["cache-control"] == "no-store"
    assert client.get("/api/voices/preview/audio/" + "b" * 32, headers=authorized()).status_code == 404

    # The UI must not be handed a corrupt file if cached audio is damaged.
    (studio.data_dir() / "voice-samples" / "thorsten.wav").write_bytes(b"invalid WAV")
    assert client.get("/api/voices/preview/audio/" + task["id"], headers=authorized()).status_code == 404


def test_preview_duration_validation_and_multisentence_assembly(client, monkeypatch):
    from tts_audiobook_tool.web_studio import production
    monkeypatch.setattr(production, "_model_identity", lambda: "synthetic-thorsten")
    monkeypatch.setattr(production, "SAMPLE_SENTENCES",
                        ("Der Anteil beträgt 3,5 %.", *production.SAMPLE_SENTENCES[1:]))
    calls = []
    def synthetic_speech(text, path, on_progress=None):
        calls.append(text)
        _test_preview_wav(path)
        return 1.0
    monkeypatch.setattr(production, "_synthesize", synthetic_speech)
    for invalid in (0, 1, 12, 90, -1):
        response = client.post("/api/voices/preview/start", headers=authorized(),
                               json={"duration_seconds": invalid})
        assert response.status_code == 422
    assert production.SAMPLE_LENGTHS == {5: 1, 15: 3, 30: 6, 60: 12}
    sample = production.make_sample(duration_seconds=15)
    assert len(calls) == 3
    assert calls[0] == "Der Anteil beträgt drei Komma fünf Prozent."
    assert calls == [production._narration_prompt(s, {}, production.DE_NORMALIZER_VERSION)
                     for s in production.SAMPLE_SENTENCES[:3]]
    assert sample.is_file()
    with wave.open(str(sample), "rb") as reader:
        assert reader.getnframes() == 16000 * 3
        assert reader.getsampwidth() == 2
        assert reader.getnchannels() == 1
    # A new sample invalidates prior approval and does not truncate words.
    with pytest.raises(ValueError, match="5, 15, 30 oder 60"):
        production.make_sample(duration_seconds=12)


def test_preview_worker_failure_exposes_safe_summary_and_steps(client, monkeypatch):
    from tts_audiobook_tool.web_studio import production
    monkeypatch.setattr(production, "_model_identity", lambda: "test-installed-voice")

    def fail(on_progress=None, duration_seconds=5):
        assert duration_seconds == 5
        on_progress("Sprachsynthese läuft. Die GPU erzeugt die Hörprobe.")
        raise RuntimeError("private worker diagnostic and user audiobook pathname")

    monkeypatch.setattr(production, "make_sample", fail)
    response = client.post("/api/voices/preview/start", headers=authorized())
    assert response.status_code == 202
    state = _await_preview_status(client, "failed")
    assert "Worker-Log" in state["error"]
    assert "private worker diagnostic" not in str(state)
    assert any("Sprachsynthese" in entry["message"] for entry in state["events"])


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



def test_resaving_chapters_preserves_existing_pronunciation_glossary(client):
    """Actual EPUB -> save chapters -> save glossary -> save chapters again."""
    from runpod.acceptance_epub import build_epub

    uploaded = client.post(
        "/api/projects", headers=authorized(),
        files={"file": ("glossar.epub", build_epub(), "application/epub+zip")},
    )
    assert uploaded.status_code == 201, uploaded.text
    project = uploaded.json()
    project_id = project["id"]
    chapters_url = f"/api/projects/{project_id}/chapters"
    glossary_url = f"/api/projects/{project_id}/glossary"
    selection = {"start_index": project["suggested_start"], "excluded_indices": []}
    first = client.put(chapters_url, headers=authorized(), json=selection)
    assert first.status_code == 200, first.text

    entries = {"MEDDPICC": "M E D D P I C C", "MongoDB": "Mongo De Be"}
    saved = client.put(glossary_url, headers=authorized(), json={"entries": entries})
    assert saved.status_code == 200, saved.text
    assert saved.json()["update"]["status"] == "applied"
    assert client.get(glossary_url, headers=authorized()).json()["entries"] == entries

    repeated = client.put(chapters_url, headers=authorized(), json=selection)
    assert repeated.status_code == 200, repeated.text
    assert client.get(glossary_url, headers=authorized()).json()["entries"] == entries
    persisted = studio._project_dir(project_id) / "engine" / "project.json"
    stored = json.loads(persisted.read_text(encoding="utf-8"))
    assert json.loads(stored["word_substitutions_json_string"]) == entries
    assert "word_substitutions" not in stored, "Obsolete Studio shadow field must not return"


def test_premature_generation_not_exposed(client):
    assert client.get("/api/capabilities", headers=authorized()).json()["tts_generation"] is False
    assert client.post("/api/generate", headers=authorized()).status_code == 404
    result = client.get("/api/voices", headers=authorized()).json()
    assert result["ready"] == []
    assert result["candidates"][0]["status"] == "under_review"


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("downloading", "heruntergeladen"),
        ("insufficient-space", "25 GiB"),
        ("cuda-unavailable", "CUDA-Tensortest"),
        ("download-failed", "Modelldownload"),
        ("installation-failed", "Installation ist fehlgeschlagen"),
        ("skipped", "übersprungen"),
    ],
)
def test_capabilities_explain_private_runpod_install_state(client, state, expected):
    root = Path(os.environ["AUDIOBOOK_STUDIO_DATA_DIR"])
    (root / "studio-voice-install-state").write_text(state + "\n", encoding="ascii")
    result = client.get("/api/capabilities", headers=authorized())
    assert result.status_code == 200
    capabilities = result.json()
    assert capabilities["voice_installed"] is False
    assert capabilities["voice_preview_available"] is False
    assert capabilities["voice_install_state"] == state
    assert expected in capabilities["reason"]
    assert "studio-access-token" not in capabilities["reason"]


def test_capabilities_ignore_untrusted_voice_state_contents(client):
    root = Path(os.environ["AUDIOBOOK_STUDIO_DATA_DIR"])
    (root / "studio-voice-install-state").write_text(
        "unauthorized private-path /workspace/audiobook-data/studio-access-token",
        encoding="ascii",
    )
    result = client.get("/api/capabilities", headers=authorized()).json()
    assert result["voice_install_state"] == "unknown"
    assert "studio-access-token" not in result["reason"]


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
