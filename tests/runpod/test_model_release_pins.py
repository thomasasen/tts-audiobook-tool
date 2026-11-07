"""Offline regression tests for immutable RunPod model release inputs."""
from __future__ import annotations

import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class TestPinnedModelRelease(unittest.TestCase):
    def setUp(self):
        self.pins_text = (ROOT / "runpod/model-pins.env").read_text(encoding="utf-8")
        self.pins = dict(re.findall(r'^([A-Z0-9_]+)="([^"]+)"$', self.pins_text, re.M))

    def test_upstream_revisions_are_full_immutable_shas(self):
        for key in ("COSYVOICE_REVISION", "MATCHA_TTS_REVISION", "BASE_MODEL_REVISION", "THORSTEN_MODEL_REVISION"):
            with self.subTest(key=key):
                self.assertRegex(self.pins[key], r"^[0-9a-f]{40}$")
        self.assertEqual(self.pins["COSYVOICE_REVISION"], "ace7c47f41bbd303aa6bf1ea80e6f9fbd595cd40")
        self.assertEqual(self.pins["MATCHA_TTS_REVISION"], "dd9105b34bf2be2230f4aa1e4769fb586a3c824e")
        self.assertEqual(self.pins["BASE_MODEL_REVISION"], "29e01c4e8d000f4bcd70751be16fa94bf3d85a18")
        self.assertEqual(self.pins["THORSTEN_MODEL_REVISION"], "3ae98d07d33de0513dc38564ea7765783bd902a7")
        self.assertNotIn('COSYVOICE_REVISION="main"', self.pins_text)
        self.assertNotIn('THORSTEN_MODEL_REVISION="main"', self.pins_text)

    def test_installer_and_prefetch_use_same_model_revision_pins(self):
        installer = (ROOT / "runpod/install-thorsten.sh").read_text(encoding="utf-8")
        prefetch = (ROOT / "runpod/prefetch-thorsten.sh").read_text(encoding="utf-8")
        source = (ROOT / "runpod/model-source.sh").read_text(encoding="utf-8")
        for script in (installer, prefetch):
            self.assertIn('source "$SCRIPT_DIR/model-pins.env"', script)
            self.assertIn('source "$SCRIPT_DIR/model-source.sh"', script)
            self.assertIn('"$BASE_MODEL_ID" --revision "$BASE_MODEL_REVISION"', script)
            self.assertIn('"$THORSTEN_MODEL_ID"', script)
            self.assertIn('--revision "$THORSTEN_MODEL_REVISION"', script)
        self.assertIn('VERSION="$MODEL_RELEASE_ID"', installer)
        self.assertIn('PREFETCH_VERSION="$MODEL_RELEASE_ID"', prefetch)
        self.assertIn('git -C "$model_root" checkout --detach "$COSYVOICE_REVISION"', source)
        self.assertIn('git -C "$model_root" submodule update --init --recursive', source)
        self.assertIn('[[ "$submodule_head" == "$MATCHA_TTS_REVISION" ]]', source)

    def test_installer_refuses_to_trust_unmarked_or_wrong_revision_cache(self):
        installer = (ROOT / "runpod/install-thorsten.sh").read_text(encoding="utf-8")
        prefetch = (ROOT / "runpod/prefetch-thorsten.sh").read_text(encoding="utf-8")
        self.assertIn('if [[ -f "$MODEL_ROOT/.studio-ready" &&', installer)
        self.assertIn('Refusing to trust or silently rebaseline weights', installer)
        self.assertIn('Unmarked model files already exist without a verified prefetch marker', prefetch)
        self.assertIn('sha256sum --check --status', installer)
        self.assertIn('sha256sum --check --status', prefetch)


if __name__ == "__main__":
    unittest.main()
