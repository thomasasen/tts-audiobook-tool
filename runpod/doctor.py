#!/usr/bin/env python3
"""RunPod preflight and installed-environment diagnostic, stdlib only.

This check never downloads model weights or runs inference. --strict is intended
for a real GPU pod, not a GitHub Actions runner.
"""
from __future__ import annotations

import argparse
import ctypes.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


MODEL_IMPORTS = {"qwen": "qwen_tts", "moss": "transformers"}
MODEL_TORCH_VERSIONS = {"qwen": "2.8.0", "moss": "2.9.1"}


def run_command(args: list[str], timeout: int = 40) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args, capture_output=True, text=True, check=False, timeout=timeout
        )
        return {
            "ok": completed.returncode == 0,
            "output": completed.stdout.strip()[-1500:],
            "error": completed.stderr.strip()[-1500:],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "output": "", "error": str(exc)}


def inspect_model(model: str, root: Path) -> dict[str, Any]:
    exe = root / "venvs" / model / "bin" / "python"
    result: dict[str, Any] = {"python": str(exe), "installed": exe.is_file()}
    if not result["installed"]:
        return result
    # Find specs without importing heavyweight TTS models or fetching model files.
    code = (
        "import importlib.util, json, torch; "
        "print(json.dumps({'torch': torch.__version__, "
        "'cuda': torch.cuda.is_available(), "
        "'module': importlib.util.find_spec("
        + repr(MODEL_IMPORTS[model])
        + ") is not None}))"
    )
    probe = run_command([str(exe), "-c", code], timeout=60)
    result["probe_ok"] = probe["ok"]
    if probe["ok"]:
        try:
            details = json.loads(probe["output"])
            result.update(details)
            result["version_ok"] = (
                details["torch"].split("+", 1)[0] == MODEL_TORCH_VERSIONS[model]
            )
        except (KeyError, ValueError, TypeError):
            result["probe_ok"] = False
            result["error"] = "Unexpected model probe output"
    else:
        result["error"] = probe["error"]
    return result


def inspect(workspace: Path, data_root: Path) -> dict[str, Any]:
    volume_mounted = workspace.is_mount()
    gpu_binary = shutil.which("nvidia-smi")
    gpu = run_command(
        [gpu_binary, "--query-gpu=name,memory.total,driver_version",
         "--format=csv,noheader,nounits"],
        timeout=12,
    ) if gpu_binary else {"ok": False, "error": "nvidia-smi not on PATH"}
    disk_free_gb = None
    if workspace.is_dir():
        try:
            disk_free_gb = round(shutil.disk_usage(workspace).free / (1024 ** 3), 2)
        except OSError:
            pass

    return {
        "workspace": str(workspace),
        "workspace_exists": workspace.is_dir(),
        "workspace_is_mount": volume_mounted,
        "workspace_writable": os.access(workspace, os.W_OK),
        "free_gb": disk_free_gb,
        "nvidia_smi": gpu,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "git": bool(shutil.which("git")),
        "portaudio": bool(ctypes.util.find_library("portaudio")),
        "python3": sys.version.split()[0],
        "model_envs": {
            model: inspect_model(model, data_root) for model in MODEL_IMPORTS
        },
    }


def check_strict(report: dict[str, Any], required: str) -> list[str]:
    errors = []
    for label, ok in (
        ("persistent /workspace mount", report["workspace_is_mount"]),
        ("writable workspace", report["workspace_writable"]),
        ("nvidia-smi", report["nvidia_smi"]["ok"]),
        ("ffmpeg", report["ffmpeg"]),
        ("ffprobe", report["ffprobe"]),
        ("git", report["git"]),
        ("PortAudio system library", report["portaudio"]),
    ):
        if not ok:
            errors.append(f"Missing or failing: {label}")

    models = ("qwen", "moss") if required == "both" else (() if required == "none" else (required,))
    for name in models:
        result = report["model_envs"][name]
        if not (result.get("installed") and result.get("probe_ok")
                and result.get("cuda") and result.get("module") and result.get("version_ok")):
            errors.append(f"{name} venv not ready or CUDA unavailable: {result}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="fail on missing GPU, mounted volume or dependencies")
    parser.add_argument("--require-models", choices=("none", "qwen", "moss", "both"), default="none")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    workspace = Path(os.environ.get("RUNPOD_WORKSPACE", "/workspace"))
    data_root = Path(os.environ.get("AUDIOBOOK_DATA_ROOT", str(workspace / "audiobook-data")))
    report = inspect(workspace, data_root)
    errors = check_strict(report, args.require_models) if args.strict else []
    if args.json:
        print(json.dumps({"checks": report, "errors": errors}, indent=2, ensure_ascii=False))
    else:
        print("RunPod audiobook environment diagnostic")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if errors:
            print("\nFAIL:\n  " + "\n  ".join(errors), file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
