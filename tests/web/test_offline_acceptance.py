"""GPU-free acceptance through the real Studio HTTP, queue and M4B stack."""
from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import time
import wave
import zipfile
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree

import av
import httpx
import numpy as np

from runpod.acceptance_epub import build_epub


ROOT = Path(__file__).resolve().parents[2]
RATE = 24000
FRAMES = 7680
INTERRUPTED_UNIT = 4


def tone_hz(prompt: str) -> int:
    return 250 + 25 * (int.from_bytes(hashlib.sha256(prompt.encode()).digest()[:4]) % 130)


def write_tone(prompt: str, path: Path) -> None:
    positions = np.arange(FRAMES) / RATE
    pcm = (10000 * np.sin(2 * np.pi * tone_hz(prompt) * positions)).astype("<i2")
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, RATE, FRAMES, "NONE", "not compressed"))
        audio.writeframes(pcm.tobytes())


def serve(control_dir: Path, interrupt: bool) -> None:
    import uvicorn

    from tts_audiobook_tool.web_studio import app, production

    replace = os.replace

    def replace_export(source, destination):
        if Path(destination).name == "audiobook.m4b" and (control_dir / "fail-export").exists():
            raise OSError(errno.ENOSPC, "synthetic sensitive marker", str(destination))
        return replace(source, destination)

    def fake_inference(prompt, path, config, on_progress=None):
        index = int(path.name.split(".", 1)[0])
        with (control_dir / "inference.jsonl").open("a", encoding="utf-8") as trace:
            trace.write(json.dumps([index, prompt]) + "\n")
        if interrupt and index == INTERRUPTED_UNIT:
            path.write_bytes(b"unfinished synthetic WAV")
            (control_dir / "interrupted").touch()
            time.sleep(60)
            raise RuntimeError("Test did not stop the process during inference")
        write_tone(prompt, path)

    # Only model availability/identity and inference are doubled. The real
    # Studio lifespan, queue, WAV staging, validation and export all run.
    production.voice_ready = lambda: True
    production._model_identity = lambda: "e03a-synthetic-worker"
    production._config = lambda: ("synthetic-python", "synthetic-script")
    production._generate_with_worker = fake_inference
    os.replace = replace_export
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        (control_dir / "port").write_text(str(listener.getsockname()[1]), encoding="ascii")
        server = uvicorn.Server(uvicorn.Config(app.app, log_level="warning", access_log=False))
        server.run(sockets=[listener])


def wait_until(check, process, log_path):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and process.poll() is None:
        result = check()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError(f"Studio did not reach the expected state:\n{log_path.read_text(encoding='utf-8')}")


@contextmanager
def server(tmp_path, token, *, interrupt):
    port_path = tmp_path / "port"
    port_path.unlink(missing_ok=True)
    log_path = tmp_path / ("before-restart.log" if interrupt else "after-restart.log")
    env = {
        **os.environ,
        "AUDIOBOOK_STUDIO_DATA_DIR": str(tmp_path / "private"),
        "AUDIOBOOK_STUDIO_TOKEN": token,
        "AUDIOBOOK_STUDIO_ASR_MODEL": "",
        "AUDIOBOOK_STUDIO_PERSISTENT_TTS": "1",
        "CUDA_VISIBLE_DEVICES": "",
        "HF_HUB_OFFLINE": "1",
        "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]),
        "PYTHONIOENCODING": "utf-8",
    }
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), str(tmp_path), str(int(interrupt))],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            port = wait_until(
                lambda: port_path.read_text(encoding="ascii") if port_path.exists() else "",
                process, log_path,
            )
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
                trust_env=False,
            ) as client:
                def ready():
                    try:
                        return client.get("/api/health", timeout=1).is_success
                    except httpx.TransportError:
                        return False

                wait_until(ready, process, log_path)
                yield client, process, log_path
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


def checked(response):
    assert response.is_success, response.text
    return response.json()


def check_m4b(payload, plan, title):
    segments = plan["segments"]
    chapter_starts = [
        index for index, segment in enumerate(segments)
        if index == 0 or segment["chapter_id"] != segments[index - 1]["chapter_id"]
    ]
    boundaries = chapter_starts + [len(segments)]
    assert [segments[index]["title"] for index in chapter_starts] == [
        "Vorwort", "Kapitel 1", "Kapitel 2",
    ]
    duration = len(segments) * FRAMES / RATE
    aac_frame = 1024 / RATE
    with av.open(io.BytesIO(payload)) as container:
        stream, = container.streams.audio
        assert stream.codec_context.name == "aac"
        assert stream.rate == RATE and stream.channels == 1
        assert container.metadata["title"] == title
        assert abs(container.duration / av.time_base - duration) <= aac_frame
        chapters = container.chapters()
        assert len(chapters) == len(chapter_starts)
        for chapter, start, end in zip(chapters, boundaries, boundaries[1:]):
            assert chapter["metadata"]["title"] == segments[start]["title"]
            for key, index in (("start", start), ("end", end)):
                assert abs(chapter[key] * Fraction(chapter["time_base"]) - index * FRAMES / RATE) <= 0.001
        samples = np.concatenate([frame.to_ndarray().ravel() for frame in container.decode(stream)])
    assert abs(len(samples) - len(segments) * FRAMES) <= 1024
    # The center of each segment is safely clear of AAC frame boundaries.
    # Check decoded tone frequency in order, not just metadata and duration.
    frequencies = np.fft.rfftfreq(FRAMES // 2, 1 / RATE)
    for index, segment in enumerate(segments):
        middle = index * FRAMES + FRAMES // 4
        spectrum = np.abs(np.fft.rfft(samples[middle:middle + FRAMES // 2]))
        assert frequencies[spectrum.argmax()] == tone_hz(segment["speech_text"])


def test_epub_to_m4b_after_abrupt_server_restart(tmp_path):
    token = "offline-test-" + "x" * 40
    epub = build_epub(include_audit_cases=True, include_attribution_case=True)
    with zipfile.ZipFile(io.BytesIO(epub)) as archive:
        assert archive.testzip() is None
        mime = archive.infolist()[0]
        assert mime.filename == "mimetype" and mime.compress_type == zipfile.ZIP_STORED
        assert archive.read(mime) == b"application/epub+zip"
        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
        package_path = container.find(".//{*}rootfile").attrib["full-path"]
        package = ElementTree.fromstring(archive.read(package_path))
        manifest = {item.attrib["id"]: item.attrib["href"] for item in package.findall(".//{*}item")}
        assert all(f"OEBPS/{href}" in archive.namelist() for href in manifest.values())
        spine = [item.attrib["idref"] for item in package.findall(".//{*}itemref")]
        nav = ElementTree.fromstring(archive.read(f"OEBPS/{manifest['nav']}"))
        links = [item.attrib["href"] for item in nav.findall(".//{*}a")]
        assert links == [manifest[item] for item in spine if item not in {"toc", "technical-attribution"}]

    with server(tmp_path, token, interrupt=True) as (client, process, log):
        instance = checked(client.get("/api/session"))["instance_id"]
        project = checked(client.post(
            "/api/projects", files={"file": ("offline.epub", epub, "application/epub+zip")},
        ))
        route = f"/api/projects/{project['id']}"
        selected = checked(client.put(
            route + "/chapters",
            json={"start_index": project["suggested_start"], "excluded_indices": []},
        ))
        assert selected["status"] == "Vorbereitet"
        job = checked(client.post(route + "/jobs"))
        assert job["total"] > INTERRUPTED_UNIT
        job_route = f"{route}/jobs/{job['id']}"
        job_dir = tmp_path / "private" / "studio-jobs" / job["id"]
        audio_dir = job_dir / "audio"
        wait_until(lambda: (tmp_path / "interrupted").exists(), process, log)
        interrupted_job = checked(client.get(job_route))
        assert interrupted_job["status"] == "running"
        assert interrupted_job["done"] == INTERRUPTED_UNIT
        activity = checked(client.get(job_route + "/activity"))
        assert activity["status"] == "running" and 0 < activity["percent"] < 100
        assert client.get(job_route + "/download").status_code == 404
        assert len(list(audio_dir.glob("*.partial.wav"))) == 1

    # A readable JSON value with the wrong shape is still a corrupt checkpoint.
    # Also simulate a torn WAV and a missing segment before restarting.
    original = (audio_dir / "0000000.wav").read_bytes()
    (audio_dir / "0000001.json").write_text("[]", encoding="utf-8")
    (audio_dir / "0000002.wav").write_bytes(b"RIFF")
    (audio_dir / "0000003.wav").unlink()
    with server(tmp_path, token, interrupt=False) as (client, process, log):
        assert checked(client.get("/api/session"))["instance_id"] != instance

        def generated():
            current = checked(client.get(job_route))
            assert current["status"] != "failed", current
            return current if current["status"] == "generated" else None

        job = wait_until(generated, process, log)
        assert job["done"] == job["total"]
        assert abs(job["duration"] - job["total"] * FRAMES / RATE) < 1e-9
        assert not list(audio_dir.glob("*.partial.wav"))
        assert (audio_dir / "0000000.wav").read_bytes() == original
        plan = json.loads((job_dir / "plan.json").read_text(encoding="utf-8"))
        assert sum(segment["pause"]["kind"] == "chapter" for segment in plan["segments"]) == 3
        assert all(segment["pause"]["duration_ms"] is None for segment in plan["segments"])
        trace = [json.loads(line) for line in (tmp_path / "inference.jsonl").read_text(encoding="utf-8").splitlines()]
        expected = list(range(INTERRUPTED_UNIT + 1)) + list(range(1, job["total"]))
        assert trace == [[index, plan["segments"][index]["speech_text"]] for index in expected]
        for index in range(job["total"]):
            with wave.open(str(audio_dir / f"{index:07d}.wav"), "rb") as audio:
                assert audio.getparams()[:4] == (1, 2, RATE, FRAMES)
        assert len(list(audio_dir.glob("*.wav"))) == job["total"]
        failure = tmp_path / "fail-export"
        failure.touch()
        export_error = client.post(job_route + "/export")
        assert export_error.status_code == 409
        assert "synthetic sensitive marker" not in export_error.text
        assert str(tmp_path) not in export_error.text
        engine_log = tmp_path / "private" / "logs" / "studio-engine.log"
        assert "synthetic sensitive marker" not in engine_log.read_text(encoding="utf-8")
        assert checked(client.get(job_route))["status"] == "generated"
        assert not (job_dir / "audiobook.m4b.partial").exists()
        assert client.get(job_route + "/download").status_code == 404
        failure.unlink()
        assert checked(client.post(job_route + "/export"))["ready"]
        assert checked(client.get(job_route))["status"] == "completed"
        assert checked(client.get(route))["status"] == "Fertig"
        for unauthorized in ("", "Bearer invalid"):
            assert client.get(job_route + "/download", headers={"Authorization": unauthorized}).status_code == 401
        other = checked(client.post(
            "/api/projects", files={"file": ("another.epub", epub, "application/epub+zip")},
        ))
        assert client.get(f"/api/projects/{other['id']}/jobs/{job['id']}/download").status_code == 404
        response = client.get(job_route + "/download")
        assert response.status_code == 200
        check_m4b(response.content, plan, project["title"])


if __name__ == "__main__":
    serve(Path(sys.argv[1]), bool(int(sys.argv[2])))
