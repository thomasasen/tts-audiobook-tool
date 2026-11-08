"""Headless Chromium system test for the actual production React bundle.

Uses temporary private data, a valid original German EPUB and a real
Uvicorn server. No screenshots or large CI artifacts are produced.
"""
from __future__ import annotations

import io
import zipfile
import math
import os
import socket
import struct
import subprocess
import wave
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from runpod.acceptance_epub import build_epub


ROOT = Path(__file__).resolve().parents[2]
TOKEN = "browser-e2e-" + "c" * 44


def browser_pcm16_wav() -> bytes:
    """One second of real audible signed PCM-16, not a mocked zero-byte blob."""
    rate = 24000
    pcm = [int(7000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(rate)]
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(struct.pack("<" + "h" * len(pcm), *pcm))
    return out.getvalue()


def browser_aac_chapter() -> bytes:
    """Real one-second AAC/M4A from our production writer, no GPU involved."""
    import numpy as np
    from tts_audiobook_tool.sound.audio_writers import AacWriter

    with tempfile.TemporaryDirectory(prefix="chapter-e2e-") as directory:
        target = Path(directory) / "chapter.m4a"
        with AacWriter(str(target), 24000, 1, "96k") as writer:
            pcm = np.array(
                [0.2 * math.sin(2 * math.pi * 440 * i / 24000) for i in range(24000)],
                dtype=np.float32,
            )
            writer.write(pcm)
        return target.read_bytes()


def main() -> None:
    assert (ROOT / "web" / "dist" / "index.html").exists(), "Build frontend before browser test"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="german-studio-browser-") as directory:
        env = {**os.environ,
               "AUDIOBOOK_STUDIO_DATA_DIR": str(Path(directory) / "private"),
               "AUDIOBOOK_STUDIO_TOKEN": TOKEN,
               "AUDIOBOOK_STUDIO_ASR_MODEL": ""}
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "tts_audiobook_tool.web_studio.app:app",
             "--host", "127.0.0.1", "--port", str(port), "--workers", "1"],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError("Uvicorn exited before becoming ready")
                try:
                    with urlopen(base + "/api/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(0.15)
            else:
                raise RuntimeError("Server health not ready")

            source = Path(directory) / "original-german-test.epub"
            source.write_bytes(build_epub(repeat=2))
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={"width": 1390, "height": 900})
                    errors = []
                    page.on("pageerror", lambda err: errors.append(str(err)))
                    page.goto(base, wait_until="networkidle")
                    page.get_by_label("Zugangsschlüssel").fill(TOKEN)
                    page.get_by_role("button", name="Studio öffnen").click()
                    page.get_by_role("heading", name="Meine Hörbücher").wait_for()
                    page.get_by_label("EPUB-Datei auswählen").set_input_files(str(source))
                    page.get_by_role("heading", name="Kapitelübersicht").wait_for(timeout=30000)
                    # Design regression: the complex EPUB review is progressive
                    # disclosure rather than five expanded forms above chapter work.
                    assert page.get_by_role("navigation", name="Hauptnavigation").count() == 1
                    assert page.get_by_role("button", name="Text und Kapitel").get_attribute("aria-current") == "page"
                    # The synthetic EPUB may not contain any complex
                    # tables or figures; when present, review stays collapsed.
                    if page.locator(".audit-elements").count():
                        assert page.locator(".audit-elements").get_attribute("open") is None
                    assert page.locator(".audit-grid > div").count() == 8
                    assert page.evaluate(
                        "() => getComputedStyle(document.querySelector('.page-heading h1')).fontSize"
                    ) != "12px"
                    assert "Vorwort" in page.get_by_label("Erster vorgelesener Abschnitt").input_value() or \
                           page.get_by_label("Erster vorgelesener Abschnitt").input_value().isdigit()
                    assert page.get_by_text("Dieses Testbuch erklärt", exact=False).count() >= 1
                    page.get_by_role("button", name="Auswahl speichern").click()
                    page.get_by_text("Kapitelauswahl im bestehenden", exact=False).wait_for(timeout=30000)

                    page.get_by_role("button", name="Ausspracheglossar").click()
                    expected_glossary = "MEDDPICC = M E D D P I C C\nMongoDB = Mongo De Be"
                    page.get_by_label("Begriff = gesprochene Form").fill(expected_glossary)
                    page.get_by_role("button", name="Glossaränderung übernehmen").click()
                    page.get_by_text("Glossar gespeichert.", exact=True).wait_for(timeout=10000)
                    # Regression: changing tabs must reload the persisted
                    # glossary, not show an empty/stale local textarea.
                    page.get_by_role("button", name="Text und Kapitel").click()
                    page.get_by_role("button", name="Ausspracheglossar").click()
                    page.wait_for_function(
                        "(expected) => document.querySelector('#glossary')?.value === expected",
                        arg=expected_glossary, timeout=10000,
                    )
                    # Re-saving chapter choices rebuilt Project() and erased
                    # word_substitutions. Saved pronunciation must survive.
                    page.get_by_role("button", name="Text und Kapitel").click()
                    page.get_by_role("button", name="Auswahl speichern").click()
                    page.get_by_text("Kapitelauswahl im bestehenden", exact=False).wait_for(timeout=30000)
                    page.get_by_role("button", name="Ausspracheglossar").click()
                    page.wait_for_function(
                        "(expected) => document.querySelector('#glossary')?.value === expected",
                        arg=expected_glossary, timeout=10000,
                    )
                    # A running project must no longer disable the glossary
                    # editor. Its update must visibly defer until the safe
                    # segment boundary, then report the reuse count.
                    import json as json_module
                    update_state = {"poll": 0}
                    def mocked_glossary_put(route):
                        assert route.request.method == "PUT"
                        assert route.request.post_data_json["entries"]["MEDDPICC"] == "Medd Pik"
                        route.fulfill(status=200, content_type="application/json",
                            body=json_module.dumps({
                                "entries": {"MEDDPICC": "Medd Pik"},
                                "update": {"status": "pending"}
                            }))
                    def mocked_glossary_status(route):
                        update_state["poll"] += 1
                        result = {"status": "pending"}
                        if update_state["poll"] >= 1:
                            result = {"status": "applied", "affected": 1,
                                      "preserved": 17, "total": 18,
                                      "message": "1 Segment betroffen; 17 Audio-Checkpoints weiterverwendet."}
                        route.fulfill(status=200, content_type="application/json",
                                      body=json_module.dumps(result))
                    page.route("**/api/projects/*/glossary", mocked_glossary_put)
                    page.route("**/api/projects/*/glossary/update", mocked_glossary_status)
                    page.get_by_label("Begriff = gesprochene Form").fill("MEDDPICC = Medd Pik")
                    page.get_by_role("button", name="Glossaränderung übernehmen").click()
                    page.get_by_text("Änderungen angenommen.", exact=False).wait_for()
                    page.get_by_text("1 von 18 Segmenten betroffen", exact=False).wait_for(timeout=10000)
                    page.unroute("**/api/projects/*/glossary/update")
                    page.unroute("**/api/projects/*/glossary")

                    # Regression: FastAPI and upstream proxies sometimes send
                    # a plain-text 500 instead of JSON. The UI must show the
                    # HTTP status/endpoint, never a SyntaxError in the alert.
                    page.route(
                        "**/api/voices",
                        lambda route: route.fulfill(
                            status=500, content_type="text/plain",
                            body="Internal Server Error",
                        ),
                    )
                    page.get_by_role("button", name="Sprecher").click()
                    page.get_by_text("Serverfehler (HTTP 500) bei /api/voices", exact=False).wait_for()
                    assert "Unexpected token" not in page.locator(".message.error").inner_text()
                    page.unroute("**/api/voices")

                    page.route(
                        "**/api/capabilities",
                        lambda route: route.fulfill(
                            status=200, content_type="text/plain",
                            body="Internal Server Error",
                        ),
                    )
                    page.get_by_role("button", name="Sprecher").click()
                    page.get_by_text("Unerwartete Serverantwort (HTTP 200) bei /api/capabilities", exact=False).wait_for()
                    page.unroute("**/api/capabilities")

                    page.get_by_role("button", name="Sprecher").click()
                    page.get_by_text("Nicht installiert", exact=True).wait_for()
                    page.get_by_text("Thorsten noch nicht betriebsbereit", exact=True).wait_for()
                    # The single speaker should use the content width,
                    # not a 300px tile floating in an otherwise empty page.
                    assert page.locator(".voice-card").bounding_box()["width"] > 650
                    assert page.get_by_role("button", name="Echte Hörprobe erzeugen").is_disabled()

                    # A non-JSON error from the new asynchronous start endpoint
                    # must be visible; no empty audio element may be displayed.
                    page.route(
                        "**/api/capabilities",
                        lambda route: route.fulfill(
                            status=200, content_type="application/json",
                            body='{"epub_import":true,"chapter_selection":true,'
                                 '"tts_generation":false,"audio_export":true,'
                                 '"voice_preview_available":true,"voice_installed":true,"voice_approved":false,'
                                 '"reason":"Thorsten-CosyVoice3 ist installiert. Bitte die Hörprobe freigeben."}',
                        ),
                    )
                    # Speaker controls must persist for the current project.
                    # Preview immediately uses the selected speed and own
                    # *spoken* prompt; no fake emotion instruction is sent.
                    page.get_by_label("Sprechgeschwindigkeit").select_option("1.05")
                    page.get_by_label("Eigener Vorlese-Prompt (Hörprobentext)").fill(
                        "Die Qualität einer Opportunity beginnt mit guter Discovery."
                    )
                    page.get_by_role("button", name="Für das Buch speichern").click()
                    page.get_by_text("Sprechtempo und Hörprobentext für dieses Buch gespeichert.").wait_for()
                    page.get_by_role("button", name="Sprecher").click()
                    assert page.get_by_label("Sprechgeschwindigkeit").input_value() == "1.05"
                    assert "Opportunity" in page.get_by_label(
                        "Eigener Vorlese-Prompt (Hörprobentext)"
                    ).input_value()
                    page.get_by_label("Länge der Hörprobe").select_option("15")
                    assert page.get_by_label("Länge der Hörprobe").input_value() == "15"
                    def reject_preview(route):
                        payload = route.request.post_data_json
                        assert payload["duration_seconds"] == 15
                        assert payload["speed"] == 1.05
                        assert "Opportunity" in payload["preview_text"]
                        assert payload["project_id"]
                        route.fulfill(
                            status=500, content_type="text/plain",
                            body="Internal Server Error",
                        )
                    page.route("**/api/voices/preview/start", reject_preview)
                    page.get_by_role("button", name="Sprecher").click()
                    page.get_by_text("Thorsten installiert – Hörfreigabe noch offen", exact=True).wait_for()
                    page.get_by_text("Installiert · Freigabe offen", exact=True).wait_for()
                    # showVoices() updates capabilities before finally clearing busy.
                    # Wait for the final UI state instead of racing the enablement.
                    expect(page.get_by_role("button", name="Echte Hörprobe erzeugen")).to_be_enabled(timeout=15000)
                    assert page.get_by_text("Thorsten-CosyVoice3 muss lokal installiert werden").count() == 0
                    page.get_by_role("button", name="Echte Hörprobe erzeugen").click()
                    page.get_by_text("Serverfehler (HTTP 500) bei /api/voices/preview/start", exact=False).wait_for()
                    assert "Unexpected token" not in page.locator(".message.error").inner_text()
                    page.unroute("**/api/voices/preview/start")
                    page.get_by_label("Länge der Hörprobe").select_option("5")

                    # Reproduce a real long-running preview. Backend stages
                    # are not percentages, and the user can inspect a log.
                    state = {"status": "running"}
                    task_id = "c" * 32

                    def mock_status(route):
                        stage = ("Sprachsynthese läuft. Die GPU erzeugt die Hörprobe."
                                 if state["status"] == "running" else
                                 "Die Hörprobe konnte nicht erzeugt werden.")
                        payload = {
                            "id": task_id, "status": state["status"],
                            "stage": stage,
                            "events": [{"at": "2026-11-07T01:00:00+00:00",
                                        "message": "Hörprobe angefordert."},
                                       {"at": "2026-11-07T01:00:02+00:00",
                                        "message": stage}],
                            "error": "" if state["status"] == "running" else stage,
                        }
                        route.fulfill(status=200, content_type="application/json",
                                      body=__import__("json").dumps(payload))

                    page.route("**/api/voices/preview/status", mock_status)
                    page.route(
                        "**/api/voices/preview/start",
                        lambda route: route.fulfill(
                            status=202, content_type="application/json",
                            body='{"id":"' + task_id + '","status":"running",'
                                 '"stage":"Hörprobe wird vorbereitet.","events":'
                                 '[{"at":"2026-11-07T01:00:00+00:00",'
                                 '"message":"Hörprobe angefordert."}],"error":""}',
                        ),
                    )
                    page.get_by_role("button", name="Echte Hörprobe erzeugen").click()
                    page.get_by_text("Hörprobe wird vorbereitet.", exact=True).wait_for()
                    # The TTS model does not emit percentage checkpoints.
                    # Its native progress element MUST stay indeterminate.
                    preview_bar = page.get_by_label("Hörprobe wird verarbeitet (Fortschritt der Modellberechnung nicht messbar)")
                    preview_bar.wait_for()
                    assert preview_bar.get_attribute("value") is None
                    page.get_by_text("Protokoll der Hörprobe (1 Einträge)").click()
                    page.get_by_text("Hörprobe angefordert.", exact=True).wait_for()
                    state["status"] = "failed"
                    page.get_by_text("Hörprobe fehlgeschlagen", exact=True).wait_for(timeout=10000)
                    assert not page.locator(".audio-preview audio").count()
                    page.unroute("**/api/voices/preview/start")
                    page.unroute("**/api/voices/preview/status")

                    # A backend claiming audio readiness must not create an
                    # empty 0:00 player if it returns a zero-byte WAV.
                    page.route(
                        "**/api/voices/preview/start",
                        lambda route: route.fulfill(
                            status=202, content_type="application/json",
                            body='{"id":"' + ("d" * 32) + '","status":"ready",'
                                 '"stage":"Hörprobe geprüft.","events":[],"error":""}',
                        ),
                    )
                    page.route(
                        "**/api/voices/preview/audio/*",
                        lambda route: route.fulfill(
                            status=200, content_type="audio/wav", body="",
                        ),
                    )
                    page.get_by_role("button", name="Echte Hörprobe erzeugen").click()
                    page.get_by_text("Die erzeugte Hörprobe ist leer oder unvollständig.").wait_for()
                    assert page.get_by_role("button", name="Nach Anhören freigeben").is_disabled()
                    assert not page.locator(".audio-preview audio").count()
                    page.unroute("**/api/voices/preview/start")
                    page.unroute("**/api/voices/preview/audio/*")

                    # Verify REAL browser decode of a valid one-second WAV.
                    # This was missing from the original acceptance tests:
                    # previously they only checked empty/failed audio cases.
                    playable_id = "e" * 32
                    page.evaluate("""() => {
                        window.__wavUrlAudit = {created: [], revoked: []};
                        const create = URL.createObjectURL.bind(URL);
                        const revoke = URL.revokeObjectURL.bind(URL);
                        URL.createObjectURL = blob => {
                            const url = create(blob);
                            window.__wavUrlAudit.created.push({url, size: blob.size, type: blob.type});
                            return url;
                        };
                        URL.revokeObjectURL = url => {
                            window.__wavUrlAudit.revoked.push(url);
                            return revoke(url);
                        };
                    }""")
                    pcm_wav = browser_pcm16_wav()
                    page.route(
                        "**/api/voices/preview/start",
                        lambda route: route.fulfill(
                            status=202, content_type="application/json",
                            body='{"id":"' + playable_id + '","status":"ready",'
                                 '"stage":"WAV erfolgreich erzeugt.","events":[],"error":""}',
                        ),
                    )
                    page.route(
                        "**/api/voices/preview/audio/*",
                        lambda route: route.fulfill(
                            status=200, content_type="audio/wav",
                            headers={"Cache-Control": "no-store"}, body=pcm_wav,
                        ),
                    )
                    page.get_by_role("button", name="Echte Hörprobe erzeugen").click()
                    player = page.locator(".audio-preview audio")
                    player.wait_for(state="visible", timeout=15000)
                    page.wait_for_function(
                        "() => { const audio = document.querySelector('.audio-preview audio');"
                        " return !!audio && Number.isFinite(audio.duration) && audio.duration >= 0.9; }",
                        timeout=15000,
                    )
                    assert player.evaluate("(a) => a.error === null")
                    urls = page.evaluate("window.__wavUrlAudit")
                    assert len(urls["created"]) == 1
                    assert urls["created"][0]["size"] == len(pcm_wav)
                    assert urls["created"][0]["type"] == "audio/wav"
                    assert urls["created"][0]["url"] == player.get_attribute("src")
                    assert urls["created"][0]["url"] not in urls["revoked"], \
                        "Browser-ready audio must not revoke the active Blob URL."
                    assert not page.get_by_text("Der Browser kann die Hörprobe nicht abspielen").count()
                    assert page.get_by_role("button", name="Nach Anhören freigeben").is_disabled(), \
                        "Metadata alone must never approve the voice."
                    page.unroute("**/api/voices/preview/start")
                    page.unroute("**/api/voices/preview/audio/*")
                    page.unroute("**/api/capabilities")

                    page.get_by_role("button", name="Hörbuch und Export").click()
                    page.get_by_role("heading", name="Vertonung vorbereiten").wait_for()
                    assert page.get_by_role("button", name="Vertonung starten").is_disabled()

                    # Simulate only the finished cancellation response here.
                    # The backend's persisted cancellation/retry path is covered
                    # separately by real SQLite/API unit tests. This verifies
                    # the actual production UI exposes the recovery action.
                    page.route(
                        "**/api/projects/*/jobs",
                        lambda route: route.fulfill(
                            status=200, content_type="application/json",
                            body='{"jobs":[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
                                 '"project_id":"synthetic","status":"cancelled",'
                                 '"done":0,"total":3,"chapter":"","duration":0,"error":""}]}',
                        ),
                    )
                    page.route(
                        "**/api/capabilities",
                        lambda route: route.fulfill(
                            status=200, content_type="application/json",
                            body='{"epub_import":true,"chapter_selection":true,'
                                 '"tts_generation":true,"audio_export":true,'
                                 '"voice_preview_available":true,"reason":"Synthetischer UI-Test","asr":{"state":"missing","message":"ASR ist in dieser Web-Runtime nicht installiert.","enabled":false,"model":null}}',
                        ),
                    )
                    page.get_by_role("button", name="Hörbuch und Export").click()
                    restart = page.get_by_role("button", name="Neuen Versuch starten")
                    restart.wait_for()
                    assert restart.is_enabled(), "Cancelled jobs must offer a new attempt"

                    # Production must display actual progress, an elapsed
                    # clock and a rolling ETA calibrated from real checkpoints.
                    page.unroute("**/api/projects/*/jobs")
                    simulated_now = datetime.now(timezone.utc)
                    production_job = {
                        "id": "b" * 32, "project_id": "synthetic", "status": "running",
                        "done": 3, "total": 6, "chapter": "Kapitel 1",
                        "duration": 19.1, "error": "",
                        "created_at": (simulated_now - timedelta(minutes=2)).isoformat(),
                    }
                    import json
                    page.route(
                        "**/api/projects/*/jobs",
                        lambda route: route.fulfill(
                            status=200, content_type="application/json",
                            body=json.dumps({"jobs": [production_job]}),
                        ),
                    )
                    # Unlike the collection endpoint, periodic 3-second
                    # polling fetches the individual job resource.
                    page.route(
                        "**/api/projects/*/jobs/*",
                        lambda route: route.fulfill(
                            status=200, content_type="application/json",
                            body=json.dumps(production_job),
                        ),
                    )
                    activity_state = {
                        "status": "running", "done": 3, "total": 6,
                        "percent": 50, "elapsed_seconds": 120,
                        "remaining_seconds": 240, "eta_sample_count": 3,
                        "events": [{"at": simulated_now.isoformat(),
                                    "message": "3 von 6 Audiosegmenten fertig."}],
                    }
                    page.route(
                        "**/api/projects/*/jobs/*/activity",
                        lambda route: route.fulfill(
                            status=200, content_type="application/json",
                            body=json.dumps({
                                "id": "b" * 32, **activity_state,
                                "calculated_at": datetime.now(timezone.utc).isoformat(),
                            }),
                        ),
                    )
                    # One entire chapter completed while subsequent TTS
                    # work remains running. The UI must immediately expose
                    # authenticated playback and an individual M4A download.
                    import json as json_module
                    m4a = browser_aac_chapter()
                    chapter_preview = browser_pcm16_wav()
                    chapter_row = {
                        "index": 0, "title": "Vorwort", "segment_count": 3,
                        "duration_seconds": 1.0, "preview_seconds": 1.0,
                        "bytes": len(m4a),
                    }
                    page.route(
                        "**/api/projects/*/jobs/*/chapters",
                        lambda route: route.fulfill(
                            status=200, content_type="application/json",
                            body=json_module.dumps({"chapters": [chapter_row]}),
                        ),
                    )
                    page.route(
                        "**/api/projects/*/jobs/*/chapters/0/audio",
                        lambda route: route.fulfill(
                            status=200, content_type="audio/wav",
                            headers={"Content-Length": str(len(chapter_preview))},
                            body=chapter_preview,
                        ),
                    )
                    page.route(
                        "**/api/projects/*/jobs/*/chapters/0/download",
                        lambda route: route.fulfill(
                            status=200, content_type="audio/mp4",
                            headers={"Content-Length": str(len(m4a))},
                            body=m4a,
                        ),
                    )
                    page.get_by_role("button", name="Hörbuch und Export").click()
                    page.get_by_text("3 von 6 Segmenten tatsächlich fertig", exact=False).wait_for()
                    page.get_by_role("heading", name="Fertige Kapitel").wait_for()
                    page.get_by_text("1 verfügbar", exact=True).wait_for()
                    page.get_by_role("button", name="Vorhören").click()
                    chapter_player = page.get_by_label("Kapitel 1 vorhören")
                    chapter_player.wait_for(state="visible", timeout=20000)
                    page.wait_for_function(
                        "() => { const el = document.querySelector('.chapter-file-player'); "
                        "return !!el && Number.isFinite(el.duration) && el.duration > 0.5; }",
                        timeout=20000,
                    )
                    assert chapter_player.evaluate("(audio) => audio.error === null")
                    assert chapter_player.evaluate("(audio) => audio.currentSrc.startsWith('blob:')")
                    page.evaluate("""() => {
                      window.__chapterSavedName = ''
                      window.__chapterWritten = 0
                      window.__chapterClosed = false
                      window.showSaveFilePicker = async options => {
                        window.__chapterSavedName = options.suggestedName
                        return {createWritable: async () => ({
                          write: async chunk => {window.__chapterWritten += chunk.byteLength},
                          close: async () => {
                            await new Promise(resolve => setTimeout(resolve, 700))
                            window.__chapterClosed = true
                          },
                          abort: async () => {throw new Error('unexpected chapter abort')},
                        })}
                      }
                    }""")
                    page.get_by_role("button", name="M4A herunterladen").click()
                    chapter_bar = page.get_by_label("Download Kapitel 1", exact=True)
                    chapter_bar.wait_for()
                    page.wait_for_function(
                        "() => document.querySelector('progress[aria-label=\"Download Kapitel 1\"]')?.value === 99",
                        timeout=10000,
                    )
                    assert chapter_bar.get_attribute("max") == "100"
                    page.wait_for_function("() => window.__chapterClosed === true", timeout=12000)
                    assert page.evaluate("window.__chapterWritten") == len(m4a)
                    assert page.evaluate("window.__chapterSavedName").endswith("_Vorwort.m4a")
                    page.evaluate("() => {window.showSaveFilePicker = undefined}")

                    page.get_by_text("Vergangene Zeit", exact=True).wait_for()
                    page.get_by_text("Geschätzte Restzeit", exact=True).wait_for()
                    metrics = page.locator(".production-time-metrics strong")
                    assert metrics.count() == 2
                    assert "Min." in metrics.nth(0).inner_text()
                    page.wait_for_function(
                        "() => { const e = document.querySelectorAll('.production-time-metrics strong');"
                        " return e.length === 2 && e[1].textContent?.includes('Min.'); }",
                        timeout=10000,
                    )
                    speech_bar = page.get_by_label("Fertiggestellte Audiosegmente")
                    assert speech_bar.get_attribute("value") == "3"
                    assert speech_bar.get_attribute("max") == "6"
                    # The real UI must advance from polls, then retain progress
                    # while paused rather than resetting to zero.
                    production_job["done"] = 5
                    activity_state.update(done=5, percent=83)
                    page.wait_for_function(
                        "() => document.querySelector('progress[aria-label=\"Fertiggestellte Audiosegmente\"]')?.value === 5",
                        timeout=12000,
                    )
                    production_job["status"] = "paused"
                    activity_state["status"] = "paused"
                    page.get_by_role("button", name="Hörbuch und Export").click()
                    page.get_by_role("button", name="Fortsetzen").wait_for()
                    assert speech_bar.get_attribute("value") == "5"
                    production_job["status"] = "running"
                    activity_state["status"] = "running"
                    page.get_by_role("button", name="Hörbuch und Export").click()
                    page.get_by_role("button", name="Pausieren").wait_for()
                    assert speech_bar.get_attribute("value") == "5"
                    # A generated job with missing ASR cannot show a
                    # misleading 'all checked' banner in the built React UI.
                    production_job["status"] = "generated"
                    production_job["done"] = 6
                    activity_state.update(status="generated", done=6, percent=100, remaining_seconds=None)
                    page.route(
                        "**/api/projects/*/jobs/*/quality",
                        lambda route: route.fulfill(
                            status=200, content_type="application/json",
                            body=json.dumps({
                                "checked": 1, "unchecked": 5, "total": 6,
                                "issues": [{"index": 1, "status": "not_checked",
                                            "reason": "ASR ist nicht installiert.",
                                            "warnings": []}],
                            }),
                        ),
                    )
                    page.get_by_role("button", name="Hörbuch und Export").click()
                    page.get_by_text("5 nicht per ASR geprüft", exact=False).wait_for()
                    page.get_by_text("ASR ist nicht installiert.", exact=False).wait_for()
                    assert speech_bar.get_attribute("value") == "6"
                    page.unroute("**/api/projects/*/jobs/*/quality")
                    # The export bar must progress 0 (unmeasured) -> 50 -> 99
                    # (container finalization) -> 100 (durably completed).
                    production_job["status"] = "exporting"
                    activity_state.update(status="exporting", export={
                        "processed": 0, "total": 6, "percent": 0,
                        "phase": "preparing", "remaining_seconds": None,
                    })
                    page.get_by_role("button", name="Hörbuch und Export").click()
                    export_bar = page.get_by_label("Fortschritt M4B-Export")
                    export_bar.wait_for()
                    assert export_bar.get_attribute("value") is None, (
                        "No export measurements: progress must be indeterminate."
                    )
                    activity_state["export"] = {
                        "processed": 3, "total": 6, "percent": 50,
                        "phase": "processing", "remaining_seconds": 120,
                    }
                    page.wait_for_function(
                        "() => document.querySelector('progress[aria-label=\"Fortschritt M4B-Export\"]')?.value === 50",
                        timeout=12000,
                    )
                    activity_state["export"] = {
                        "processed": 6, "total": 6, "percent": 99,
                        "phase": "finalizing", "remaining_seconds": None,
                    }
                    page.wait_for_function(
                        "() => document.querySelector('progress[aria-label=\"Fortschritt M4B-Export\"]')?.value === 99",
                        timeout=12000,
                    )
                    assert "abgeschlossen" in page.get_by_text(
                        "Datei wird abgeschlossen.", exact=False
                    ).inner_text()
                    # Actual browser download path: file-save picker first,
                    # authenticated fetch, streamed writes, close and errors.
                    production_job["status"] = "completed"
                    production_job["done"] = 6
                    activity_state.update(status="completed", export={
                        "processed": 6, "total": 6, "percent": 100,
                        "phase": "completed", "remaining_seconds": None,
                    })
                    page.route(
                        "**/api/projects/*/jobs/*/download",
                        lambda route: route.fulfill(
                            status=200,
                            content_type="audio/mp4",
                            headers={"Content-Length": "64"},
                            body=b"x" * 64,
                        ),
                    )
                    page.evaluate("""() => {
                      window.__fileWritten = 0
                      window.__fileClosed = false
                      window.__fileAborted = false
                      window.showSaveFilePicker = async () => ({
                        createWritable: async () => ({
                          write: async part => { window.__fileWritten += part.byteLength },
                          close: async () => {
                            await new Promise(resolve => setTimeout(resolve, 700))
                            window.__fileClosed = true
                          },
                          abort: async () => { window.__fileAborted = true },
                        }),
                      })
                    }""")
                    page.get_by_role("button", name="Hörbuch und Export").click()
                    assert export_bar.get_attribute("value") == "100"
                    page.get_by_role("button", name="M4B herunterladen").click()
                    m4b_bar = page.get_by_label("M4B-Download", exact=True)
                    m4b_bar.wait_for()
                    page.wait_for_function(
                        "() => document.querySelector('progress[aria-label=\"M4B-Download\"]')?.value === 99",
                        timeout=10000,
                    )
                    assert "gespeichert" in m4b_bar.get_attribute("aria-valuetext")
                    assert m4b_bar.get_attribute("max") == "100"
                    page.wait_for_function("() => window.__fileClosed === true", timeout=10000)
                    assert page.evaluate("window.__fileWritten") == 64
                    assert not page.evaluate("window.__fileAborted")
                    page.get_by_text("direkt gespeichert", exact=False).wait_for()
                    # ZIP has a streaming response, so it must expose live
                    # bytes and an honest determinate or indeterminate bar.
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_STORED) as archive:
                        archive.writestr("001 - Vorwort.m4a", m4a)
                    archive_bytes = zip_buffer.getvalue()
                    page.route(
                        "**/api/projects/*/jobs/*/chapters/archive",
                        lambda route: route.fulfill(
                            status=200, content_type="application/zip",
                            body=archive_bytes,
                        ),
                    )
                    page.evaluate("""() => {
                      window.__zipWritten = 0
                      window.__zipClosed = false
                      window.showSaveFilePicker = async () => ({
                        createWritable: async () => ({
                          write: async part => { window.__zipWritten += part.byteLength },
                          close: async () => {
                            await new Promise(resolve => setTimeout(resolve, 700))
                            window.__zipClosed = true
                          },
                          abort: async () => {throw Error('Unexpected ZIP abort')},
                        })
                      })
                    }""")
                    page.get_by_role("button", name="Alle Kapitel als ZIP herunterladen").click()
                    zip_bar = page.get_by_label("Download aller Kapitel", exact=True)
                    zip_bar.wait_for()
                    assert zip_bar.get_attribute("max") == "100"
                    # A known-size mocked ZIP may reach 99% during the last
                    # disk write; an unknown-size stream remains indeterminate.
                    assert zip_bar.get_attribute("value") in (None, "99")
                    page.wait_for_function("() => window.__zipClosed === true", timeout=10000)
                    assert page.evaluate("window.__zipWritten") == len(archive_bytes)
                    page.get_by_text("Kapitel als ZIP gespeichert", exact=False).wait_for()
                    page.unroute("**/api/projects/*/jobs/*/chapters/archive")
                    # Unsupported browser with a large response fails before
                    # buffering any of the advertised 97 MiB into a Blob.
                    page.evaluate("() => { window.showSaveFilePicker = undefined }")
                    page.unroute("**/api/projects/*/jobs/*/download")
                    page.route(
                        "**/api/projects/*/jobs/*/download",
                        lambda route: route.fulfill(
                            status=200, content_type="audio/mp4",
                            headers={"Content-Length": str(97 * 1024 * 1024)},
                            body=b"x" * 64,
                        ),
                    )
                    page.get_by_role("button", name="M4B herunterladen").click()
                    page.get_by_text("überschreitet 96 MB", exact=False).wait_for()
                    page.unroute("**/api/projects/*/jobs/*/download")
                    page.unroute("**/api/projects/*/jobs/*/activity")
                    page.unroute("**/api/projects/*/jobs/*/chapters/0/audio")
                    page.unroute("**/api/projects/*/jobs/*/chapters/0/download")
                    page.unroute("**/api/projects/*/jobs/*/chapters")
                    page.unroute("**/api/projects/*/jobs/*")
                    page.unroute("**/api/projects/*/jobs")

                    page.get_by_role("button", name="Dunkles Design").click()
                    assert "dark" in page.locator(".shell").get_attribute("class")
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.get_by_role("navigation", name="Hauptnavigation").is_visible()
                    assert page.locator(".sidebar").evaluate(
                        "(element) => getComputedStyle(element).overflowX"
                    ) == "auto"
                    page.wait_for_timeout(250)
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), \
                           "Unexpected horizontal overflow at mobile width"
                    page.get_by_role("button", name="Abmelden").click()
                    page.get_by_role("heading", name="Geschützter Zugang").wait_for()
                    assert not errors, "Browser runtime errors: " + repr(errors)
                finally:
                    browser.close()
            print("CHROMIUM E2E PASSED: Login, real EPUB, chapter save, glossary, locked voice, export gate, mobile.")
        finally:
            server.terminate()
            try:
                server.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
                server.communicate(timeout=5)


if __name__ == "__main__":
    main()
