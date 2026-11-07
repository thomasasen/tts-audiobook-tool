"""Offline tests for pinned Hugging Face downloads and upstream integrity checks."""
from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load_runpod_script(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "runpod" / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


class FakeModelApi:
    def __init__(self, siblings_by_repo, revisions_by_repo=None):
        self.siblings_by_repo = siblings_by_repo
        self.revisions_by_repo = revisions_by_repo or {}
        self.calls = []

    def model_info(self, repo_id, revision, files_metadata):
        self.calls.append((repo_id, revision, files_metadata))
        return SimpleNamespace(
            sha=self.revisions_by_repo.get(repo_id, revision),
            siblings=self.siblings_by_repo[repo_id],
        )


class TestPinnedModelDownloads(unittest.TestCase):
    def setUp(self):
        self.downloader = load_runpod_script("download-hf-models.py", "runpod_download_hf_models_test")
        self.verifier = load_runpod_script("verify-hf-models.py", "runpod_verify_hf_models_test")
        self.base_id = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
        self.thorsten_id = "Thorsten-Voice/CosyVoice3"
        self.base_revision = "a" * 40
        self.thorsten_revision = "b" * 40

    def _create_files(self, model_dir: Path):
        contents = {}
        for name in self.verifier.BASE_FILES:
            contents[name] = f"base:{name}".encode()
        contents["CosyVoice-BlankEN/tokenizer.json"] = b'{"tokenizer":"test"}'
        contents["CosyVoice-BlankEN/vocab.json"] = b'{"hello":1}'
        for name in self.verifier.THORSTEN_FILES:
            contents[name] = f"thorsten:{name}".encode()
        for name, data in contents.items():
            path = model_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return contents

    def _api_for(self, contents, revision_override=None):
        siblings = {self.base_id: [], self.thorsten_id: []}
        base_names = (*self.verifier.BASE_FILES, "CosyVoice-BlankEN/tokenizer.json", "CosyVoice-BlankEN/vocab.json")
        for repo_id, names in (
            (self.base_id, base_names),
            (self.thorsten_id, self.verifier.THORSTEN_FILES),
        ):
            for index, name in enumerate(names):
                data = contents[name]
                if index % 2 == 0:
                    lfs = SimpleNamespace(sha256=hashlib.sha256(data).hexdigest())
                    blob_id = None
                else:
                    lfs = None
                    blob_id = git_blob_sha1(data)
                siblings[repo_id].append(SimpleNamespace(rfilename=name, lfs=lfs, blob_id=blob_id))
        revisions = {}
        if revision_override:
            revisions[self.base_id] = revision_override
        return FakeModelApi(siblings, revisions)

    def test_downloader_fetches_only_runtime_assets_and_overlays_thorsten(self):
        calls = []

        def fake_snapshot_download(repo_id, revision, local_dir, allow_patterns, max_workers):
            target = Path(local_dir)
            target.mkdir(parents=True, exist_ok=True)
            calls.append({
                "repo_id": repo_id,
                "revision": revision,
                "allow_patterns": tuple(allow_patterns),
                "max_workers": max_workers,
            })
            if repo_id == self.base_id:
                names = self.downloader.BASE_REQUIRED
            else:
                names = self.downloader.THORSTEN_REQUIRED
            for name in names:
                path = target / name
                if name == "CosyVoice-BlankEN":
                    (path / "tokenizer.json").parent.mkdir(parents=True, exist_ok=True)
                    (path / "tokenizer.json").write_text("{}", encoding="utf-8")
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(f"{repo_id}:{name}".encode())
            return str(target)

        with tempfile.TemporaryDirectory() as temp:
            model_dir = Path(temp) / "model"
            with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(snapshot_download=fake_snapshot_download)}):
                self.downloader.download_models(
                    model_dir,
                    self.base_id,
                    self.base_revision,
                    self.thorsten_id,
                    self.thorsten_revision,
                    max_workers=3,
                )
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]["revision"], self.base_revision)
            self.assertEqual(calls[1]["revision"], self.thorsten_revision)
            self.assertEqual(calls[0]["max_workers"], 3)
            self.assertIn("CosyVoice-BlankEN/**", calls[0]["allow_patterns"])
            self.assertEqual(set(calls[1]["allow_patterns"]), set(self.downloader.THORSTEN_ALLOW_PATTERNS))
            self.assertTrue((model_dir / "CosyVoice-BlankEN/tokenizer.json").is_file())
            self.assertTrue((model_dir / "llm.pt").is_file())
            self.assertFalse((model_dir / ".download-base").exists())
            self.assertFalse((model_dir / ".download-thorsten").exists())

    def test_downloader_refuses_nonempty_model_directory_and_mutable_revisions(self):
        with tempfile.TemporaryDirectory() as temp:
            model_dir = Path(temp) / "model"
            model_dir.mkdir()
            (model_dir / "partial.pt").write_bytes(b"partial")
            with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(snapshot_download=lambda **_: None)}):
                with self.assertRaises(FileExistsError):
                    self.downloader.download_models(
                        model_dir, self.base_id, self.base_revision,
                        self.thorsten_id, self.thorsten_revision,
                    )
            empty_dir = Path(temp) / "empty"
            with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(snapshot_download=lambda **_: None)}):
                with self.assertRaises(ValueError):
                    self.downloader.download_models(
                        empty_dir, self.base_id, "main", self.thorsten_id, self.thorsten_revision,
                    )

    def test_verifier_checks_lfs_and_git_blob_hashes_and_writes_sha256_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            model_dir = Path(temp) / "model"
            model_dir.mkdir()
            contents = self._create_files(model_dir)
            api = self._api_for(contents)
            output = Path(temp) / "checksums.txt"
            fake_hub = SimpleNamespace(HfApi=lambda: api)
            with patch.dict(sys.modules, {"huggingface_hub": fake_hub}):
                result = self.verifier.verify_and_write(
                    model_dir, self.base_id, self.base_revision,
                    self.thorsten_id, self.thorsten_revision, output, api=api,
                )
            self.assertEqual(len(result), len(contents))
            self.assertEqual(len(output.read_text(encoding="utf-8").splitlines()), len(contents))
            self.assertTrue(all(len(value) == 64 for value in result.values()))
            self.assertEqual(api.calls, [
                (self.base_id, self.base_revision, True),
                (self.thorsten_id, self.thorsten_revision, True),
            ])

    def test_verifier_fails_closed_on_corrupt_file_or_wrong_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            model_dir = Path(temp) / "model"
            model_dir.mkdir()
            contents = self._create_files(model_dir)
            api = self._api_for(contents)
            (model_dir / "llm.pt").write_bytes(b"corrupted")
            output = Path(temp) / "checksums.txt"
            with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(HfApi=lambda: api)}):
                with self.assertRaisesRegex(ValueError, "failed"):
                    self.verifier.verify_and_write(
                        model_dir, self.base_id, self.base_revision,
                        self.thorsten_id, self.thorsten_revision, output, api=api,
                    )
            self.assertFalse(output.exists())

            contents = self._create_files(model_dir)
            wrong_api = self._api_for(contents, revision_override="f" * 40)
            with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(HfApi=lambda: wrong_api)}):
                with self.assertRaisesRegex(ValueError, "upstream resolved"):
                    self.verifier.verify_and_write(
                        model_dir, self.base_id, self.base_revision,
                        self.thorsten_id, self.thorsten_revision, output, api=wrong_api,
                    )
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
