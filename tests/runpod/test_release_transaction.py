"""Offline tests for the atomic RunPod release backup and rollback transaction."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("runpod_release_transaction_test", ROOT / "runpod" / "release_transaction.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load release_transaction.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class TestReleaseTransaction(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.venvs = self.data / "venvs"
        self.repo = self.root / "repo"
        self.data.mkdir()
        self.venvs.mkdir()
        (self.repo / "web").mkdir(parents=True)
        (self.repo / "runpod").mkdir()
        (self.repo / "runpod" / "model-pins.env").write_text('MODEL_RELEASE_ID="test-v1"\n', encoding="utf-8")
        (self.repo / "README.md").write_text("old-code", encoding="utf-8")
        (self.repo / "web" / "dist").mkdir()
        (self.repo / "web" / "dist" / "index.html").write_text("old-ui", encoding="utf-8")

        self.uv = self.data / "tools" / "uv-bootstrap" / "bin" / "uv"
        self.uv.parent.mkdir(parents=True)
        self.uv.write_text(
            "#!/usr/bin/env bash\n"
            'if [[ "$1" == "pip" && "$2" == "freeze" ]]; then printf "fake-package==1.0\\n"; fi\n',
            encoding="utf-8",
        )
        self.uv.chmod(0o755)
        self.freeze_hash = hashlib.sha256(b"fake-package==1.0\n").hexdigest()

        self.model_root = self.data / "models" / "CosyVoice"
        self.model_dir = self.model_root / "pretrained_models" / "CosyVoice3-0.5B"
        self.model_dir.mkdir(parents=True)
        self.model_file = self.model_dir / "llm.pt"
        self.model_file.write_bytes(b"old-model-weights")
        (self.model_root / ".studio-ready").write_text("test-release-v1\n", encoding="utf-8")
        model_hash = hashlib.sha256(self.model_file.read_bytes()).hexdigest()
        (self.data / "models" / "thorsten-model-sha256.txt").write_text(
            f"{model_hash}  llm.pt\n", encoding="utf-8"
        )
        (self.data / "models" / "thorsten-runtime-sha256.txt").write_text(
            self.freeze_hash + "\n", encoding="utf-8"
        )

        self._create_env("thorsten", marker=None, manifest=None)
        self._create_env("web", marker=".studio-web-runtime-ready", manifest=".studio-web-runtime-packages.sha256")
        self._create_env("qwen", marker=".runpod-ready", manifest=".runpod-packages.sha256")

    def tearDown(self):
        self.temp.cleanup()

    def _create_env(self, name, marker, manifest):
        env = self.venvs / name
        (env / "bin").mkdir(parents=True)
        python = env / "bin" / "python"
        python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        python.chmod(0o755)
        (env / "release-payload.txt").write_text(f"old-{name}", encoding="utf-8")
        if marker:
            (env / marker).write_text(f"{name}-fingerprint\n", encoding="utf-8")
            (env / manifest).write_text(self.freeze_hash + "\n", encoding="utf-8")
        return env

    def _create_new_release(self):
        model_root = self.data / "models" / "CosyVoice"
        model_dir = model_root / "pretrained_models" / "CosyVoice3-0.5B"
        model_dir.mkdir(parents=True)
        (model_dir / "llm.pt").write_bytes(b"new-model-weights")
        (model_root / ".studio-ready").write_text("test-release-v2\n", encoding="utf-8")
        digest = hashlib.sha256((model_dir / "llm.pt").read_bytes()).hexdigest()
        (self.data / "models" / "thorsten-model-sha256.txt").write_text(
            f"{digest}  llm.pt\n", encoding="utf-8"
        )
        (self.data / "models" / "thorsten-runtime-sha256.txt").write_text(
            self.freeze_hash + "\n", encoding="utf-8"
        )
        for name in ("thorsten", "web", "qwen"):
            self._create_env(name, marker=None, manifest=None)
        (self.uv.parent / "uv").write_text(
            "#!/usr/bin/env bash\n"
            'if [[ "$1" == "pip" && "$2" == "freeze" ]]; then printf "fake-package==1.0\\n"; fi\n',
            encoding="utf-8",
        )
        self.uv.chmod(0o755)
        dist = self.repo / "web" / "dist"
        (dist / "index.html").write_text("new-ui", encoding="utf-8")

    def test_backup_and_restore_preserve_previous_release_and_snapshot_replaced_release(self):
        backup = release.backup_release(self.data, self.venvs, self.repo, "release-old")
        self.assertFalse(self.model_root.exists())
        self.assertFalse((self.venvs / "thorsten").exists())
        self.assertTrue((backup / "BACKUP_COMPLETE").is_file())
        metadata = json.loads((backup / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["state"], "available")
        self.assertTrue(metadata["assets"]["venvs/qwen"]["present"])
        self.assertEqual((backup / "repo" / "web-dist" / "index.html").read_text(), "old-ui")

        self._create_new_release()
        pre_id = release.restore_release(self.data, self.venvs, self.repo, "release-old")
        self.assertTrue((self.model_dir / "llm.pt").is_file())
        self.assertEqual((self.model_dir / "llm.pt").read_bytes(), b"old-model-weights")
        self.assertEqual((self.venvs / "thorsten" / "release-payload.txt").read_text(), "old-thorsten")
        self.assertEqual((self.venvs / "web" / "release-payload.txt").read_text(), "old-web")
        self.assertEqual((self.venvs / "qwen" / "release-payload.txt").read_text(), "old-qwen")
        self.assertEqual((self.repo / "web" / "dist" / "index.html").read_text(), "old-ui")

        restored_metadata = json.loads((backup / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(restored_metadata["state"], "restored")
        pre_backup = self.data / "releases" / "tts-audiobook-tool" / pre_id
        self.assertTrue((pre_backup / "assets" / "models" / "CosyVoice").is_dir())
        self.assertEqual((pre_backup / "repo" / "web-dist" / "index.html").read_text(), "new-ui")
        available = [item["backup_id"] for item in release.list_backups(self.data) if item["state"] == "available"]
        self.assertIn(pre_id, available)

    def test_backup_refuses_corrupt_or_unmarked_release(self):
        self.model_file.write_bytes(b"corrupted")
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            release.backup_release(self.data, self.venvs, self.repo, "bad-release")
        self.assertTrue(self.model_root.exists())

        (self.model_file).write_bytes(b"old-model-weights")
        (self.model_root / ".studio-ready").unlink()
        with self.assertRaisesRegex(RuntimeError, "release-ready"):
            release.backup_release(self.data, self.venvs, self.repo, "unmarked-release")

    def test_backup_id_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            release.backup_release(self.data, self.venvs, self.repo, "../outside")

    def test_restore_refuses_code_mismatch_without_explicit_override(self):
        import subprocess

        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "old"], check=True)
        backup = release.backup_release(self.data, self.venvs, self.repo, "code-bound-release")
        old_commit = json.loads((backup / "metadata.json").read_text())["repo_commit"]
        (self.repo / "README.md").write_text("new-code", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "new"], check=True)
        with self.assertRaisesRegex(RuntimeError, "Application code is"):
            release.restore_release(self.data, self.venvs, self.repo, "code-bound-release")
        self.assertTrue((backup / "BACKUP_COMPLETE").is_file())
        self.assertEqual(json.loads((backup / "metadata.json").read_text())["repo_commit"], old_commit)


if __name__ == "__main__":
    unittest.main()
