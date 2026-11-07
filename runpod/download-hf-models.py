#!/usr/bin/env python3
"""Download only the pinned CosyVoice3 runtime assets required by this worker."""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable

BASE_ALLOW_PATTERNS = (
    "hift.pt",
    "cosyvoice3.yaml",
    "campplus.onnx",
    "speech_tokenizer_v3.onnx",
    "speech_tokenizer_v3.batch.onnx",
    "CosyVoice-BlankEN/**",
)
THORSTEN_ALLOW_PATTERNS = (
    "llm.pt",
    "flow.pt",
    "spk2info.pt",
    "infer_thorsten.py",
)
BASE_REQUIRED = (
    "hift.pt",
    "cosyvoice3.yaml",
    "campplus.onnx",
    "speech_tokenizer_v3.onnx",
    "speech_tokenizer_v3.batch.onnx",
    "CosyVoice-BlankEN",
)
THORSTEN_REQUIRED = THORSTEN_ALLOW_PATTERNS


def _validate_revision(revision: str, label: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise ValueError(f"{label} must be an immutable hexadecimal commit SHA")


def _move_selected_files(source: Path, destination: Path, names: Iterable[str]) -> None:
    for name in names:
        src = source / name
        dst = destination / name
        if not src.exists():
            raise FileNotFoundError(f"Expected downloaded asset is missing: {name}")
        if dst.exists():
            raise FileExistsError(f"Refusing to overwrite an existing model asset: {dst}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))


def download_models(
    model_dir: Path,
    base_model_id: str,
    base_revision: str,
    thorsten_model_id: str,
    thorsten_revision: str,
    max_workers: int = 4,
) -> None:
    from huggingface_hub import snapshot_download

    _validate_revision(base_revision, "Base-model revision")
    _validate_revision(thorsten_revision, "Thorsten-model revision")
    if not 1 <= max_workers <= 8:
        raise ValueError("max_workers must be between 1 and 8")
    model_dir.mkdir(parents=True, exist_ok=True)
    if any(model_dir.iterdir()):
        raise FileExistsError(
            f"Model directory is not empty; refusing to mix or overwrite partial assets: {model_dir}"
        )

    base_tmp = model_dir / ".download-base"
    thorsten_tmp = model_dir / ".download-thorsten"
    try:
        print(f"Downloading selected base assets from {base_model_id}@{base_revision[:12]}.")
        snapshot_download(
            repo_id=base_model_id,
            revision=base_revision,
            local_dir=str(base_tmp),
            allow_patterns=list(BASE_ALLOW_PATTERNS),
            max_workers=max_workers,
        )
        _move_selected_files(base_tmp, model_dir, BASE_REQUIRED)
        shutil.rmtree(base_tmp, ignore_errors=True)

        print(f"Downloading Thorsten fine-tune assets from {thorsten_model_id}@{thorsten_revision[:12]}.")
        snapshot_download(
            repo_id=thorsten_model_id,
            revision=thorsten_revision,
            local_dir=str(thorsten_tmp),
            allow_patterns=list(THORSTEN_ALLOW_PATTERNS),
            max_workers=max_workers,
        )
        _move_selected_files(thorsten_tmp, model_dir, THORSTEN_REQUIRED)
        shutil.rmtree(thorsten_tmp, ignore_errors=True)
    except Exception:
        # Keep any partial download directory for inspection. The installers
        # deliberately fail closed on a non-empty, unmarked model directory.
        raise

    for name in (*BASE_REQUIRED[:-1], *THORSTEN_REQUIRED):
        if not (model_dir / name).is_file():
            raise FileNotFoundError(f"Required model file missing after download: {name}")
    if not (model_dir / "CosyVoice-BlankEN").is_dir():
        raise FileNotFoundError("Required CosyVoice-BlankEN tokenizer directory missing after download")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--base-model-id", required=True)
    parser.add_argument("--base-revision", required=True)
    parser.add_argument("--thorsten-model-id", required=True)
    parser.add_argument("--thorsten-revision", required=True)
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()
    try:
        download_models(
            args.model_dir,
            args.base_model_id,
            args.base_revision,
            args.thorsten_model_id,
            args.thorsten_revision,
            args.max_workers,
        )
    except Exception as exc:  # noqa: BLE001 - concise, fail-closed operator diagnostic
        print(f"Pinned model download failed: {exc}", file=sys.stderr)
        return 1
    print("Pinned base and Thorsten model assets downloaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
