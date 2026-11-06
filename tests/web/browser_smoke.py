"""Headless Chromium system test for the actual production React bundle.

Uses temporary private data, a valid original German EPUB and a real
Uvicorn server. No screenshots or large CI artifacts are produced.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright
from runpod.acceptance_epub import build_epub


ROOT = Path(__file__).resolve().parents[2]
TOKEN = "browser-e2e-" + "c" * 44


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

                    page.get_by_role("button", name="Sprecher").click()
                    page.get_by_text("Freigabe offen").wait_for()
                    assert page.get_by_role("button", name="Echte Hörprobe erzeugen").is_disabled()

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
                                 '"voice_preview_available":true,"reason":"Synthetischer UI-Test"}',
                        ),
                    )
                    page.get_by_role("button", name="Hörbuch und Export").click()
                    restart = page.get_by_role("button", name="Neuen Versuch starten")
                    restart.wait_for()
                    assert restart.is_enabled(), "Cancelled jobs must offer a new attempt"

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
