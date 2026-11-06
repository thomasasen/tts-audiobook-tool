#!/usr/bin/env python3
"""GPU acceptance against the *real* protected German Audiobook Studio API.

Never approves a voice and never treats an automated quality check as a human
listening test. All generated EPUBs contain original synthetic text. Reports,
WAVs and M4Bs stay on the private RunPod volume, never in GitHub artifacts.

Examples:
  python runpod/acceptance.py preflight
  python runpod/acceptance.py start --repeat 25
  python runpod/acceptance.py status --checkpoint
  # Restart the Studio/Pod (manually), then:
  python runpod/acceptance.py status --verify-restart
  python runpod/acceptance.py finish
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from acceptance_epub import build_epub

PORT = os.environ.get("AUDIOBOOK_STUDIO_PORT", "7860")
if not PORT.isdigit() or not 1 <= int(PORT) <= 65535:
    raise RuntimeError("Ungültiger lokaler AUDIOBOOK_STUDIO_PORT.")
BASE = "http://127.0.0.1:" + PORT
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def root() -> Path:
    directory = Path(os.environ.get("AUDIOBOOK_STUDIO_DATA_DIR", "/workspace/audiobook-data")).resolve()
    if not directory.is_dir():
        raise RuntimeError(f"Persistentes Datenverzeichnis fehlt: {directory}")
    location = directory / "acceptance"
    location.mkdir(mode=0o700, parents=True, exist_ok=True)
    return location


def json_file(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def state_path() -> Path:
    return root() / "current.json"


def state() -> dict:
    path = state_path()
    if not path.is_file():
        raise RuntimeError("Kein laufender Prüflauf vorhanden. Zuerst 'start' ausführen.")
    return json.loads(path.read_text(encoding="utf-8"))


def token() -> str:
    secret = os.environ.get("AUDIOBOOK_STUDIO_TOKEN", "")
    if not secret:
        path = root().parent / "studio-access-token"
        if path.is_file():
            secret = path.read_text(encoding="utf-8").strip()
    if len(secret) < 32:
        raise RuntimeError("Studio-Zugangsschlüssel fehlt oder ist ungültig.")
    return secret


def api(route: str, *, method: str = "GET", value: dict | None = None,
        binary: bytes | None = None, media_type: str | None = None,
        raw: bool = False):
    if not route.startswith("/") or route.startswith("//"):
        raise ValueError("Nur lokale API-Pfade sind erlaubt.")
    headers = {"Authorization": "Bearer " + token(), "Accept": "application/json"}
    body = None
    if value is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(value).encode("utf-8")
    if binary is not None:
        body = binary
        headers["Content-Type"] = media_type or "application/octet-stream"
    request = Request(BASE + route, headers=headers, data=body, method=method)
    try:
        with urlopen(request, timeout=120) as response:
            if raw:
                return response.read(MAX_DOWNLOAD_BYTES + 1)
            return json.loads(response.read(1024 * 1024))
    except HTTPError as exc:
        exc.read(4096)  # Drain a bounded error body without revealing private text.
        # Never echo private user book text, even if an upstream error includes it.
        raise RuntimeError(f"HTTP {exc.code} bei {route}; Details im privaten Serverlog.") from exc
    except URLError as exc:
        raise RuntimeError("Lokaler Webserver nicht erreichbar (Port 7860).") from exc


def hardware() -> dict:
    command = ["nvidia-smi", "--query-gpu=name,memory.total,memory.used",
               "--format=csv,noheader,nounits"]
    try:
        output = subprocess.run(command, capture_output=True, text=True, timeout=12, check=True)
        row = output.stdout.strip().splitlines()[0].split(",")
        return {"gpu": row[0].strip(), "vram_total_mib": int(row[1]),
                "vram_used_mib": int(row[2])}
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return {"error": "Keine betriebsbereite NVIDIA-GPU gefunden."}


def preflight() -> dict:
    hw = hardware()
    health = api("/api/health")
    session = api("/api/session")
    voices = api("/api/voices")
    caps = api("/api/capabilities")
    result = {"timestamp_utc": timestamp(), "hardware": hw,
              "health": health.get("status"), "language": session.get("language"),
              "instance": session.get("instance_id"),
              "ready_voices": [item["id"] for item in voices.get("ready", [])],
              "tts_available": caps.get("tts_generation", False)}
    json_file(root() / "preflight.json", result)
    if "error" in hw or result["health"] != "ok" or result["language"] != "de":
        raise RuntimeError("Preflight fehlgeschlagen: GPU, Backend oder Sprache prüfen.")
    if not result["tts_available"]:
        raise RuntimeError("Noch keine freigegebene Stimme. Thorsten im Studio erzeugen, anhören und manuell freigeben.")
    return result


def start(repeat: int) -> dict:
    check = preflight()
    if state_path().exists():
        old = state()
        if not old.get("finished", False):
            raise RuntimeError("Es gibt bereits einen unvollständigen Prüflauf. Status oder Finish verwenden.")
    epub = build_epub(repeat=repeat)
    boundary = "GermanStudioAcceptance" + uuid.uuid4().hex
    content = (("--" + boundary + "\r\n"
                'Content-Disposition: form-data; name="file"; filename="test-german.epub"\r\n'
                "Content-Type: application/epub+zip\r\n\r\n").encode("ascii") + epub
               + ("\r\n--" + boundary + "--\r\n").encode("ascii"))
    project = api("/api/projects", method="POST", binary=content,
                  media_type="multipart/form-data; boundary=" + boundary)
    chapters = project["chapters"]
    candidates = [c["index"] for c in chapters if c["title"].lower().startswith("vorwort")]
    if not candidates:
        raise RuntimeError("Echter EPUB-Parser hat das Vorwort nicht erkannt.")
    start_index = candidates[0]
    excluded = [c["index"] for c in chapters if c["is_toc"]]
    saved = api(f'/api/projects/{project["id"]}/chapters', method="PUT",
                value={"start_index": start_index, "excluded_indices": excluded})
    if saved["status"] != "Vorbereitet":
        raise RuntimeError("Kapitelauswahl konnte nicht gespeichert werden.")
    job = api(f'/api/projects/{project["id"]}/jobs', method="POST")
    data = {"version": 1, "created_at": timestamp(), "started_unix": time.time(),
            "project_id": project["id"], "job_id": job["id"],
            "start_index": start_index, "total": job["total"],
            "repeat": repeat, "gpu": check["hardware"],
            "initial_instance": check["instance"], "finished": False}
    json_file(state_path(), data)
    return {"project_id": data["project_id"], "job_id": data["job_id"],
            "segment_count": data["total"], "status": job["status"],
            "info": "Für einen Recovery-Test während der Generierung den Dienst/Pod neu starten."}


def get_status() -> tuple[dict, dict]:
    current = state()
    job = api(f'/api/projects/{current["project_id"]}/jobs/{current["job_id"]}')
    return current, job


def status(*, checkpoint: bool = False, verify_restart: bool = False) -> dict:
    current, job = get_status()
    session = api("/api/session")
    instance = session.get("instance_id")
    data = {
        "checked_at": timestamp(), "job_id": current["job_id"], "status": job["status"],
        "done": job["done"], "total": job["total"],
        "audio_seconds": job["duration"], "current_chapter": job["chapter"],
        "server_instance": instance,
    }
    if checkpoint:
        if job["status"] not in ("running", "queued") or not 1 <= job["done"] < job["total"]:
            raise RuntimeError("Checkpoint nur bei laufendem, noch nicht abgeschlossenem Auftrag mit mindestens einem Segment setzen.")
        current["checkpoint"] = {"instance": instance, "done": job["done"],
                                  "created_at": timestamp()}
        json_file(state_path(), current)
        data["checkpoint"] = "gespeichert; Dienst jetzt neu starten"
    if verify_restart:
        previous = current.get("checkpoint")
        if not previous:
            raise RuntimeError("Zuerst bei laufendem Auftrag --checkpoint ausführen.")
        if instance == previous["instance"]:
            raise RuntimeError("Kein Neustart des Studio-Servers nachgewiesen (Instanz-ID unverändert).")
        if job["done"] < previous["done"]:
            raise RuntimeError("Datenverlust erkannt: Fortschritt hinter dem Checkpoint.")
        if job["status"] in ("failed", "cancelled"):
            raise RuntimeError("Auftrag konnte nach Neustart nicht fortgesetzt werden.")
        # A new server ID with an unchanged queued job does not prove resume.
        # Require fresh progress or a genuinely finished production after restart.
        if job["done"] == previous["done"] and job["status"] not in ("generated", "completed"):
            raise RuntimeError(
                "Server wurde neu gestartet, aber kein neuer Generierungsfortschritt nachgewiesen. "
                "Status später erneut prüfen, während der Auftrag weiterläuft."
            )
        data["recovery_verified"] = True
        current["recovery_verified"] = True
        json_file(state_path(), current)
    json_file(root() / "last-status.json", data)
    return data


def finish() -> dict:
    current, job = get_status()
    if job["status"] not in ("generated", "completed"):
        raise RuntimeError("Produktion ist noch nicht vollständig: " + job["status"])
    route = f'/api/projects/{current["project_id"]}/jobs/{current["job_id"]}'
    if job["status"] == "generated":
        api(route + "/export", method="POST")
    import av
    local = root() / ("review-" + current["job_id"] + ".m4b")
    response = api(route + "/download", raw=True)
    if len(response) > MAX_DOWNLOAD_BYTES:
        raise RuntimeError("M4B zu groß für diesen Prüfmodus (512 MB); direkt auf Volume prüfen.")
    local.write_bytes(response)
    os.chmod(local, 0o600)
    with av.open(str(local)) as container:
        tracks = list(container.streams.audio)
        chapters = list(container.chapters())
        if len(tracks) != 1:
            raise RuntimeError("M4B hat nicht genau eine Audiospur.")
        titles = [c["metadata"].get("title", "") for c in chapters]
        if titles != ["Vorwort", "Kapitel 1", "Kapitel 2"]:
            raise RuntimeError("Kapitelmarken stimmen nicht: " + repr(titles))
        duration = 0
        for frame in container.decode(audio=0):
            duration += frame.samples / frame.sample_rate
        if duration < 3:
            raise RuntimeError("M4B enthält zu wenig decodierbares Audio.")
    current["finished"] = True
    current["completed_at"] = timestamp()
    current["duration_seconds"] = duration
    current["render_elapsed_seconds"] = round(time.time() - current["started_unix"], 1)
    # Use per-segment inference durations, not wall time across manual listening
    # pauses or a pod restart. Only the private local segment metadata is read.
    segments = root().parent / "studio-jobs" / current["job_id"] / "audio"
    timings = []
    for path in sorted(segments.glob("*.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(meta.get("inference_seconds"), (int, float)):
            timings.append(float(meta["inference_seconds"]))
    current["inference_segments_measured"] = len(timings)
    current["inference_seconds"] = round(sum(timings), 1)
    # Historical 'inference_seconds' includes first model load and retries.
    # Name the ratio accurately: it is not isolated decoder GPU-inference time.
    current["rtf_generation_wall"] = (round(sum(timings) / duration, 3)
                                      if len(timings) == job["total"] else None)
    current["m4b_size_bytes"] = local.stat().st_size
    current["chapter_titles"] = titles
    current["final_gpu"] = hardware()
    current["audio_review_required"] = True
    json_file(state_path(), current)
    return {
        "result": "Technischer EPUB→M4B-Workflow bestanden; HÖRABNAHME NOCH OFFEN",
        "m4b": str(local), "chapters": titles, "audio_seconds": round(duration, 1),
        "real_time_factor_generation_wall": current["rtf_generation_wall"],
        "inference_segments_measured": current["inference_segments_measured"],
        "wall_time_including_manual_interruption_s": current["render_elapsed_seconds"],
        "recovery_verified": current.get("recovery_verified", False),
        "listen_to_output": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Echter RunPod GPU-/EPUB-/M4B-Integrationstest")
    parser.add_argument("phase", choices=("preflight", "start", "status", "finish"))
    parser.add_argument("--repeat", type=int, default=3,
                        help="Anzahl der wiederholten eigenen Absätze (1–50); 25+ für Restart")
    parser.add_argument("--checkpoint", action="store_true")
    parser.add_argument("--verify-restart", action="store_true")
    options = parser.parse_args()
    try:
        if options.phase == "preflight":
            result = preflight()
        elif options.phase == "start":
            result = start(options.repeat)
        elif options.phase == "status":
            result = status(checkpoint=options.checkpoint,
                            verify_restart=options.verify_restart)
        else:
            result = finish()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print("NICHT BESTANDEN: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
