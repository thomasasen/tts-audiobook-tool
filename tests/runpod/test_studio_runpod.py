"""Static checks for integrated Web + Thorsten RunPod bootstrap.

No GPU, model downloads, or network required.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class TestGermanStudioRunPod(unittest.TestCase):
    def test_new_install_and_start_scripts_are_valid_bash(self):
        for relative in ("runpod/install-thorsten.sh", "runpod/start-web.sh"):
            with self.subTest(relative=relative):
                subprocess.run(["bash", "-n", str(ROOT / relative)], check=True)

    def test_install_is_persistent_and_not_checking_in_weights(self):
        source = (ROOT / "runpod/install-thorsten.sh").read_text(encoding="utf-8")
        self.assertIn("CosyVoice.git", source)
        self.assertIn("ace7c47", source)
        self.assertIn("Thorsten-Voice/CosyVoice3", source)
        self.assertIn('"$AUDIOBOOK_DATA_ROOT/models', source)
        self.assertIn("spk2info.pt", source)
        self.assertIn("3.11", source)
        self.assertIn('sha256sum --check --status "$CHECKSUMS"', source)
        self.assertIn("Refusing to trust or silently rebaseline weights", source)

    def test_huggingface_transfer_fallback_in_runpod_environment(self):
        # The official GPU image can enable hf_transfer even when the isolated
        # Thorsten virtualenv does not contain the optional package.
        env = (ROOT / "runpod/env.sh").resolve()
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; printf "%s" "$HF_HUB_ENABLE_HF_TRANSFER"', "bash", str(env)],
            env={**os.environ, "HF_HUB_ENABLE_HF_TRANSFER": "1"},
            text=True, capture_output=True, check=True,
        )
        self.assertEqual(result.stdout, "0")
        installer = (ROOT / "runpod/install-thorsten.sh").read_text(encoding="utf-8")
        self.assertIn('source "$SCRIPT_DIR/env.sh"', installer)

    def test_existing_verified_model_repairs_pkg_resources_without_redownload(self):
        # Simulates a persistent RunPod volume with valid weights but a
        # post-install setuptools upgrade that dropped pkg_resources.
        with tempfile.TemporaryDirectory(prefix="thorsten-repair-") as work:
            root = Path(work) / "audiobook-data"
            model = root / "models/CosyVoice"
            weights = model / "pretrained_models/CosyVoice3-0.5B"
            weights.mkdir(parents=True)
            for name in ("llm.pt", "flow.pt", "spk2info.pt", "hift.pt"):
                (weights / name).write_bytes(b"verified fixture for " + name.encode())
            (model / "infer_thorsten.py").write_text("# fixture\\n")
            (model / ".studio-ready").write_text(
                "CosyVoice-ace7c47-Thorsten-CosyVoice3-2026-11-v1\\n"
            )
            checksums = root / "models/thorsten-model-sha256.txt"
            checksums.write_text("".join(
                f"{hashlib.sha256((weights / name).read_bytes()).hexdigest()}  {name}\\n"
                for name in ("llm.pt", "flow.pt", "spk2info.pt", "hift.pt")
            ))
            uv = root / "tools/uv-bootstrap/bin/uv"
            uv.parent.mkdir(parents=True)
            uv.write_text("#!/bin/sh\\nexit 88\\n")
            uv.chmod(0o755)
            python = root / "venvs/thorsten/bin/python"
            python.parent.mkdir(parents=True)
            python.write_text("""#!/bin/sh
if [ "$1" = "-W" ]; then
  test -f "$FAKE_PKG_READY"
  exit $?
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
  printf '%s\\n' "$*" >> "$FAKE_PIP_AUDIT"
  case "$*" in *setuptools==80.9.0*)
    touch "$FAKE_PKG_READY"
    exit 0 ;;
  esac
fi
exit 99
""")
            python.chmod(0o755)
            sentinel = Path(work) / "pkg-resources-present"
            audit = Path(work) / "pip-audit.txt"
            env = {
                **os.environ,
                "RUNPOD_WORKSPACE": work,
                "AUDIOBOOK_DATA_ROOT": str(root),
                "FAKE_PKG_READY": str(sentinel),
                "FAKE_PIP_AUDIT": str(audit),
            }
            installer = str(ROOT / "runpod/install-thorsten.sh")
            first = subprocess.run(
                ["bash", installer], env=env, cwd=work,
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertIn("Repairing Thorsten runtime", first.stdout)
            self.assertIn("weights verified", first.stdout)
            self.assertIn("setuptools==80.9.0", audit.read_text())
            self.assertTrue(sentinel.exists())
            second = subprocess.run(
                ["bash", installer], env=env, cwd=work,
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(audit.read_text().count("setuptools==80.9.0"), 1)
            self.assertIn("weights verified", second.stdout)

    def test_setuptools_is_pinned_in_fresh_install(self):
        script = (ROOT / "runpod/install-thorsten.sh").read_text(encoding="utf-8")
        self.assertIn('"setuptools==80.9.0"', script)
        self.assertNotIn("--upgrade setuptools", script)
        self.assertLess(
            script.index("ensure_thorsten_pkg_resources\\n"),
            script.index('if [[ -f "$MODEL_ROOT/.studio-ready"'),
        )

    def test_readme_command_points_to_exact_integrated_autostart(self):
        root = (ROOT / "README.md").read_text(encoding="utf-8")
        autostart = (ROOT / "runpod/autostart.sh").read_text(encoding="utf-8")
        self.assertIn(
            "master/runpod/autostart.sh", root
        )
        self.assertIn(
            'RUNPOD_TTS_BRANCH:=master', autostart
        )
        self.assertIn("runpod/start-web.sh", autostart)
        self.assertIn("runpod/install-thorsten.sh", autostart)

    def test_web_is_access_key_protected_single_process(self):
        code = (ROOT / "runpod/start-web.sh").read_text(encoding="utf-8")
        self.assertIn("studio-access-token", code)
        self.assertIn("chmod 600", code)
        self.assertIn("AUDIOBOOK_STUDIO_TOKEN", code)
        self.assertIn("--workers 1", code)
        self.assertIn("AUDIOBOOK_THORSTEN_PYTHON", code)
        self.assertIn("AUDIOBOOK_THORSTEN_SCRIPT", code)


if __name__ == "__main__":
    unittest.main()
