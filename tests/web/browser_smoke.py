"""Headless Chromium system test for the actual production React bundle.

Uses temporary private data, a valid original German EPUB and a real
Uvicorn server. No screenshots or large CI artifacts are produced.
"""
from __future__ import annotations

import io
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

from playwright.sync_api import sync_playwright
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
                    assert "Vorwort" in page.get_by_label("Erster vorgelesener Abschnitt").input_value() or \
                           page.get_by_label("Erster vorgelesener Abschnitt").input_value().isdigit()
                    assert page.get_by_text("Dieses Testbuch erklärt", exact=False).count() >= 1
                    page.get_by_role("button", name="Auswahl speichern").click()
                    page.get_by_text("Kapitelauswahl im bestehenden", exact=False).wait_for(timeout=30000)

                    page.get_by_role("button", name="Ausspracheglossar").click()
                    page.get_by_label("Begriff = gesprochene Form").fill("MEDDPICC = M E D D P I C C")
                    page.get_by_role("button", name="Glossar speichern").click()
                    page.get_by_text("Ausspracheglossar wurde aktualisiert", exact=False).wait_for(timeout=10000)

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
                    page.get_by_label("Länge der Hörprobe").select_option("15")
                    assert page.get_by_label("Länge der Hörprobe").input_value() == "15"
                    def reject_preview(route):
                        assert route.request.post_data_json["duration_seconds"] == 15
                        route.fulfill(
                            status=500, content_type="text/plain",
                            body="Internal Server Error",
                        )
                    page.route("**/api/voices/preview/start", reject_preview)
                    page.get_by_role("button", name="Sprecher").click()
                    page.get_by_text("Thorsten installiert – Hörfreigabe noch offen", exact=True).wait_for()
                    page.get_by_text("Installiert · Freigabe offen", exact=True).wait_for()
                    assert page.get_by_role("button", name="Echte Hörprobe erzeugen").is_enabled()
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
                    page.route(
                        "**/api/projects/*/jobs/*/activity",
                        lambda route: route.fulfill(
                            status=200, content_type="application/json",
                            body=json.dumps({
                                "id": "b" * 32, "status": "running", "done": 3, "total": 6,
                                "percent": 50, "elapsed_seconds": 120,
                                "remaining_seconds": 240, "eta_sample_count": 3,
                                "calculated_at": datetime.now(timezone.utc).isoformat(),
                                "events": [{"at": simulated_now.isoformat(),
                                            "message": "3 von 6 Audiosegmenten fertig."}],
                            }),
                        ),
                    )
                    page.get_by_role("button", name="Hörbuch und Export").click()
                    page.get_by_text("3 von 6 Segmenten tatsächlich fertig", exact=False).wait_for()
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
                    assert page.get_by_label("Fertiggestellte Audiosegmente").get_attribute("value") == "3"
                    # A generated job with missing ASR cannot show a
                    # misleading 'all checked' banner in the built React UI.
                    production_job["status"] = "generated"
                    production_job["done"] = 6
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
                    page.unroute("**/api/projects/*/jobs/*/quality")
                    page.unroute("**/api/projects/*/jobs/*/activity")
                    page.unroute("**/api/projects/*/jobs")

                    page.get_by_role("button", name="Dunkles Design").click()
                    assert "dark" in page.locator(".shell").get_attribute("class")
                    page.set_viewport_size({"width": 390, "height": 844})
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
