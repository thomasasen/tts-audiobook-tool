"""Offline first-start integration: no network, no GPU, no real model weights.

Validates the real prefetch shell script's sequential base/fine-tune overlay,
pinned revisions, SHA256 ready marker, repeatability and installer handoff.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PREFETCH = ROOT / "runpod/prefetch-thorsten.sh"
INSTALL = ROOT / "runpod/install-thorsten.sh"


def model_pins() -> dict[str, str]:
    return dict(re.findall(
        r'^([A-Z0-9_]+)="([^"]+)"$',
        (ROOT / "runpod/model-pins.env").read_text(encoding="utf-8"),
        re.M,
    ))


class TestFirstStart(unittest.TestCase):
    def fixture(self, root: Path):
        data = root / "audiobook-data"
        model = data / "models/CosyVoice"
        weights = model / "pretrained_models/CosyVoice3-0.5B"
        weights.mkdir(parents=True)
        (model / ".git").mkdir()
        (model / "third_party/Matcha-TTS").mkdir(parents=True)
        (model / "requirements.txt").write_text("torch>=2\nopenai-whisper\n", encoding="utf-8")
        pins = model_pins()

        uv = data / "tools/uv-bootstrap/bin/uv"
        uv.parent.mkdir(parents=True)
        venv = data / "venvs/thorsten/bin"
        venv.mkdir(parents=True)
        fake = root / "bin"
        fake.mkdir()

        git = fake / "git"
        git.write_text("""#!/bin/sh
if [ "$1" = "-C" ]; then
  target="$2"; shift 2
  case "$target" in
    "$FAKE_MODEL_ROOT")
      case "$*" in
        "remote get-url origin") echo "$FAKE_COSYVOICE_ORIGIN"; exit 0 ;;
        "rev-parse HEAD") echo "$FAKE_COSYVOICE_REVISION"; exit 0 ;;
        "status --porcelain --untracked-files=no") exit 0 ;;
        "fetch --no-tags origin "*) exit 0 ;;
        "checkout --detach "*) exit 0 ;;
        "submodule sync --recursive") exit 0 ;;
        "submodule update --init --recursive") exit 0 ;;
      esac ;;
    "$FAKE_MODEL_ROOT/third_party/Matcha-TTS")
      case "$*" in
        "rev-parse HEAD") echo "$FAKE_MATCHA_TTS_REVISION"; exit 0 ;;
      esac ;;
  esac
fi
exit 0
""", encoding="utf-8")
        git.chmod(0o755)

        apt = fake / "apt-get"
        apt.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        apt.chmod(0o755)

        fake_modules = root / "fake-modules"
        hub_package = fake_modules / "huggingface_hub"
        hub_package.mkdir(parents=True)
        (hub_package / "__init__.py").write_text("""from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

def _load_records():
    path = Path(os.environ["FAKE_HF_METADATA"])
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))

def snapshot_download(repo_id, revision, local_dir, allow_patterns, max_workers):
    destination = Path(local_dir)
    destination.mkdir(parents=True, exist_ok=True)
    if "FunAudioLLM" in repo_id:
        names = (
            "hift.pt", "cosyvoice3.yaml", "campplus.onnx",
            "speech_tokenizer_v3.onnx", "speech_tokenizer_v3.batch.onnx",
            "CosyVoice-BlankEN/tokenizer.json",
        )
    elif "Thorsten-Voice" in repo_id:
        names = ("llm.pt", "flow.pt", "spk2info.pt", "infer_thorsten.py")
    else:
        raise AssertionError(f"Unexpected model repository: {repo_id}")
    files = {}
    for name in names:
        payload = f"{repo_id}@{revision}:{name}".encode("utf-8")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        files[name] = hashlib.sha256(payload).hexdigest()
    record = {"repo_id": repo_id, "revision": revision, "files": files}
    records = _load_records()
    records.append(record)
    Path(os.environ["FAKE_HF_METADATA"]).write_text(
        json.dumps(records, sort_keys=True), encoding="utf-8"
    )
    with Path(os.environ["FAKE_DOWNLOAD_AUDIT"]).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "repo_id": repo_id,
            "revision": revision,
            "allow_patterns": list(allow_patterns),
            "max_workers": max_workers,
        }) + "\\n")
    return str(destination)

class HfApi:
    def model_info(self, repo_id, revision, files_metadata=True):
        record = next(
            item for item in _load_records()
            if item["repo_id"] == repo_id and item["revision"] == revision
        )
        siblings = [
            SimpleNamespace(
                rfilename=name,
                lfs=SimpleNamespace(sha256=digest),
                blob_id=None,
            )
            for name, digest in record["files"].items()
        ]
        return SimpleNamespace(sha=revision, siblings=siblings)
""", encoding="utf-8")

        uv.write_text("""#!/bin/sh
if [ "$1" = "--version" ]; then echo "uv 0.13.7"; exit 0; fi
if [ "$1" = "tool" ] && [ "$2" = "run" ]; then
  shift 2
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --python|--with) shift 2 ;;
      python) shift; exec python3 "$@" ;;
      *) echo "Unexpected uv tool argument: $1" >&2; exit 90 ;;
    esac
  done
fi
if [ "$1" = "sync" ]; then exit 0; fi
if [ "$1" = "pip" ] && [ "$2" = "freeze" ]; then echo "fake-package==1.0"; exit 0; fi
if [ "$*" = "python install 3.11.18" ]; then exit 0; fi
exit 90
""", encoding="utf-8")
        uv.chmod(0o755)

        py = venv / "python"
        py.write_text("""#!/bin/sh
if [ "$1" = "-c" ]; then echo "3.11.18"; exit 0; fi
exit 0
""", encoding="utf-8")
        py.chmod(0o755)

        hf = venv / "hf"
        hf.write_text("#!/bin/sh\nprintf '%s\n' 'UNEXPECTED DOWNLOAD' >> \"$FAKE_DOWNLOAD_AUDIT\"; exit 78\n", encoding="utf-8")
        hf.chmod(0o755)

        env = {
            **os.environ,
            "PATH": str(fake) + os.pathsep + os.environ["PATH"],
            "RUNPOD_WORKSPACE": str(root),
            "AUDIOBOOK_DATA_ROOT": str(data),
            "RUNPOD_TTS_DOWNLOAD_WORKERS": "4",
            "FAKE_DOWNLOAD_AUDIT": str(root / "downloads.jsonl"),
            "FAKE_HF_METADATA": str(root / "hf-metadata.json"),
            "PYTHONPATH": str(fake_modules) + os.pathsep + os.environ.get("PYTHONPATH", ""),
            "FAKE_MODEL_DIR": str(weights),
            "FAKE_MODEL_ROOT": str(model),
            "FAKE_COSYVOICE_ORIGIN": pins["COSYVOICE_GIT_URL"],
            "FAKE_COSYVOICE_REVISION": pins["COSYVOICE_REVISION"],
            "FAKE_MATCHA_TTS_REVISION": pins["MATCHA_TTS_REVISION"],
        }
        return env, data, model, weights

    def run_shell(self, path: Path, env: dict):
        return subprocess.run(
            ["bash", str(path)], env=env, capture_output=True, text=True,
            check=False, timeout=20,
        )

    def test_prefetch_is_ordered_integrity_checked_and_reused_by_installer(self):
        if not (ROOT / "runpod/locks/thorsten/uv.lock").is_file():
            self.skipTest("The immutable Thorsten uv.lock is generated by the A03 lock workflow.")
        with tempfile.TemporaryDirectory() as directory:
            env, data, model, weights = self.fixture(Path(directory))
            first = self.run_shell(PREFETCH, env)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            audit = Path(env["FAKE_DOWNLOAD_AUDIT"])
            entries = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
            pins = model_pins()
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["repo_id"], pins["BASE_MODEL_ID"])
            self.assertEqual(entries[0]["revision"], pins["BASE_MODEL_REVISION"])
            self.assertEqual(entries[1]["repo_id"], pins["THORSTEN_MODEL_ID"])
            self.assertEqual(entries[1]["revision"], pins["THORSTEN_MODEL_REVISION"])
            self.assertEqual(entries[0]["max_workers"], 4)
            self.assertEqual(entries[1]["max_workers"], 4)
            self.assertIn("CosyVoice-BlankEN/**", entries[0]["allow_patterns"])
            self.assertEqual(entries[1]["allow_patterns"], ["llm.pt", "flow.pt", "spk2info.pt", "infer_thorsten.py"])
            self.assertTrue((weights / "llm.pt").read_text().startswith(pins["THORSTEN_MODEL_ID"] + "@"))
            self.assertTrue((weights / "hift.pt").read_text().startswith(pins["BASE_MODEL_ID"] + "@"))
            self.assertTrue((model / ".prefetch-complete").is_file())
            self.assertTrue((model / ".prefetch-sha256.txt").is_file())

            second = self.run_shell(PREFETCH, env)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn("no download", second.stdout)
            self.assertEqual(len(audit.read_text(encoding="utf-8").splitlines()), 2)

            installed = self.run_shell(INSTALL, env)
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            self.assertIn("skipping both model downloads", installed.stdout)
            self.assertEqual(len(audit.read_text(encoding="utf-8").splitlines()), 2)
            self.assertTrue((model / ".studio-ready").is_file())
            manifest = data / "models/thorsten-model-sha256.txt"
            self.assertTrue(manifest.is_file())
            for row in manifest.read_text(encoding="utf-8").splitlines():
                expected, filename = row.split(maxsplit=1)
                self.assertEqual(hashlib.sha256((weights / filename).read_bytes()).hexdigest(), expected)

    def test_corrupted_completed_prefetch_is_not_silently_accepted(self):
        if not (ROOT / "runpod/locks/thorsten/uv.lock").is_file():
            self.skipTest("The immutable Thorsten uv.lock is generated by the A03 lock workflow.")
        with tempfile.TemporaryDirectory() as directory:
            env, data, model, weights = self.fixture(Path(directory))
            first = self.run_shell(PREFETCH, env)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            (weights / "llm.pt").write_text("unexpected bytes", encoding="utf-8")
            failed = self.run_shell(INSTALL, env)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("checksum/version invalid", failed.stderr)
            self.assertFalse((model / ".studio-ready").exists())
            self.assertEqual(len(Path(env["FAKE_DOWNLOAD_AUDIT"]).read_text(encoding="utf-8").splitlines()), 2)

    def test_readme_docker_start_command_is_copyable_and_targets_master(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        match = re.search(
            r"### Docker Start Command[^\n]*\n.*?\n```bash\n([^\n]+)\n```",
            text, flags=re.S,
        )
        self.assertIsNotNone(match, "A single-line command must appear in README")
        command = match.group(1)
        syntax = subprocess.run(["bash", "-n", "-c", command], capture_output=True, text=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        self.assertIn("master/runpod/autostart.sh", command)
        self.assertIn("RUNPOD_TTS_BRANCH=master", command)
        self.assertIn("RUNPOD_TTS_MODELS=none", command)
        self.assertIn("RUNPOD_TTS_START_TUI=0", command)
        self.assertIn("RUNPOD_TTS_DOWNLOAD_WORKERS=4", command)
        self.assertIn('wait "$pod_pid"', command)
        self.assertNotIn("feature/runpod-p0", command)

    def test_every_relevant_shell_script_syntax_compiles(self):
        for name in (
            "env.sh", "model-source.sh", "setup.sh", "install-web.sh", "start-web.sh",
            "build-web.sh", "prefetch-thorsten.sh", "install-thorsten.sh",
            "generate-release-locks.sh", "autostart.sh",
        ):
            with self.subTest(name=name):
                run = subprocess.run(
                    ["bash", "-n", str(ROOT / "runpod" / name)],
                    capture_output=True, text=True,
                )
                self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
