"""Offline contracts for GPU acceptance: synthetic fixture and fail-closed CLI."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from runpod.acceptance_epub import build_epub

ROOT = Path(__file__).resolve().parents[2]


class TestRunPodLiveAcceptanceContract(unittest.TestCase):
    def test_original_fixture_is_valid_epub_three_spoken_sections(self):
        archive_bytes = build_epub(repeat=2)
        with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
            self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
            self.assertIn(b"application/oebps-package+xml",
                          archive.read("META-INF/container.xml"))
            opf = archive.read("OEBPS/content.opf").decode("utf-8")
            self.assertIn('idref="foreword"', opf)
            self.assertIn('idref="chapter1"', opf)
            self.assertIn('idref="chapter2"', opf)
            self.assertIn("Prüfbuch für deutsche Hörbücher", opf)
            self.assertIn("Dieses Testbuch", archive.read("OEBPS/foreword.xhtml").decode("utf-8"))

    def test_original_fixture_covers_subheadings_lists_and_technical_language(self):
        archive_bytes = build_epub(repeat=2)
        with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
            one = archive.read("OEBPS/chapter1.xhtml").decode("utf-8")
            two = archive.read("OEBPS/chapter2.xhtml").decode("utf-8")
        self.assertIn("<h2>Messbare Kriterien</h2>", one)
        self.assertIn("<ul>", one)
        self.assertEqual(one.count("<li>"), 3)
        self.assertIn("CRM-System", one)
        self.assertIn("2.500 Euro", one)
        self.assertIn("Forecast-Review", one)
        self.assertIn("2027", two)
        self.assertIn("Business Case", two)
        self.assertIn("&#8222;", two)

    def test_fixture_repeat_guard(self):
        with self.assertRaises(ValueError):
            build_epub(repeat=0)
        with self.assertRaises(ValueError):
            build_epub(repeat=51)

    def test_cli_help_is_safe_without_any_gpu_or_token(self):
        run = subprocess.run([sys.executable, str(ROOT / "runpod/acceptance.py"), "--help"],
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("--verify-restart", run.stdout)
        self.assertIn("preflight", run.stdout)

    def test_cli_refuses_to_report_success_without_live_runpod(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = {**os.environ,
                   "AUDIOBOOK_STUDIO_DATA_DIR": str(Path(temporary) / "missing"),
                   "AUDIOBOOK_STUDIO_TOKEN": "x" * 48}
            run = subprocess.run([sys.executable, str(ROOT / "runpod/acceptance.py"), "preflight"],
                                 capture_output=True, text=True, env=env, timeout=10)
            self.assertEqual(run.returncode, 1)
            self.assertIn("NICHT BESTANDEN", run.stderr)
            self.assertFalse((Path(temporary) / "missing").exists())

    def test_review_gate_never_auto_approves_voice(self):
        source = (ROOT / "runpod/acceptance.py").read_text(encoding="utf-8")
        self.assertNotIn("/api/voices/approve", source)
        self.assertIn("instance_id", source)
        self.assertIn("recovery_verified", source)
        self.assertIn("listen_to_output", source)


if __name__ == "__main__":
    unittest.main()
