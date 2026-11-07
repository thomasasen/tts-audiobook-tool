#!/usr/bin/env python3
"""Verify pinned public Hugging Face model files against upstream revision metadata.

The local SHA-256 manifest is written only after every required file has been
matched to the immutable revision's LFS SHA-256 or Git blob object ID.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

BASE_FILES = (
    "hift.pt",
    "cosyvoice3.yaml",
    "campplus.onnx",
    "speech_tokenizer_v3.onnx",
    "speech_tokenizer_v3.batch.onnx",
)
BASE_PREFIXES = ("CosyVoice-BlankEN/",)
THORSTEN_FILES = (
    "llm.pt",
    "flow.pt",
    "spk2info.pt",
    "infer_thorsten.py",
)
CHUNK_SIZE = 1024 * 1024


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _hash_file(path: Path, algorithm: str, prefix: bytes = b"") -> str:
    digest = hashlib.new(algorithm)
    digest.update(prefix)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_blob_sha1(path: Path) -> str:
    size = path.stat().st_size
    return _hash_file(path, "sha1", f"blob {size}\0".encode("ascii"))


def _verify_one(repo_id: str, revision: str, filename: str, sibling: Any, local_path: Path) -> str:
    if not local_path.is_file():
        raise ValueError(f"Required local model file is missing: {local_path}")
    lfs = _field(sibling, "lfs")
    lfs_sha = _field(lfs, "sha256") if lfs is not None else None
    if lfs_sha:
        upstream_hash = str(lfs_sha).lower()
        actual_hash = _hash_file(local_path, "sha256")
        algorithm = "LFS SHA-256"
    else:
        blob_id = _field(sibling, "blob_id")
        if not blob_id:
            raise ValueError(f"{repo_id}@{revision}: upstream checksum metadata missing for {filename}")
        upstream_hash = str(blob_id).lower()
        actual_hash = _git_blob_sha1(local_path)
        algorithm = "Git blob SHA-1"
    if actual_hash != upstream_hash:
        raise ValueError(
            f"{repo_id}@{revision}: {filename} failed {algorithm} verification "
            f"(expected {upstream_hash}, got {actual_hash})"
        )
    return _hash_file(local_path, "sha256")


def _verify_repository(
    api: Any,
    repo_id: str,
    revision: str,
    model_dir: Path,
    filenames: tuple[str, ...],
    prefixes: tuple[str, ...] = (),
) -> dict[str, str]:
    if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise ValueError(f"{repo_id}: revision is not an immutable hexadecimal commit SHA")
    info = api.model_info(repo_id, revision=revision, files_metadata=True)
    resolved_sha = _field(info, "sha")
    if resolved_sha != revision:
        raise ValueError(f"{repo_id}: upstream resolved {resolved_sha!r}, expected {revision}")
    siblings = {_field(item, "rfilename"): item for item in (_field(info, "siblings") or [])}
    selected = set(filenames)
    for name in siblings:
        if any(name.startswith(prefix) for prefix in prefixes):
            selected.add(name)
    for prefix in prefixes:
        if not any(name.startswith(prefix) for name in selected):
            raise ValueError(f"{repo_id}@{revision}: required asset directory is empty: {prefix}")
    result: dict[str, str] = {}
    for filename in sorted(selected):
        sibling = siblings.get(filename)
        if sibling is None:
            raise ValueError(f"{repo_id}@{revision}: required upstream file is absent: {filename}")
        result[filename] = _verify_one(repo_id, revision, filename, sibling, model_dir / filename)
    return result


def verify_and_write(
    model_dir: Path,
    base_model_id: str,
    base_revision: str,
    thorsten_model_id: str,
    thorsten_revision: str,
    output: Path,
    api: Any | None = None,
) -> dict[str, str]:
    from huggingface_hub import HfApi

    model_dir = model_dir.resolve()
    if not model_dir.is_dir():
        raise ValueError(f"Model directory does not exist: {model_dir}")
    api = api or HfApi()
    checksums = _verify_repository(
        api, base_model_id, base_revision, model_dir, BASE_FILES, BASE_PREFIXES
    )
    checksums.update(
        _verify_repository(api, thorsten_model_id, thorsten_revision, model_dir, THORSTEN_FILES)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = "".join(f"{checksums[name]}  {name}\n" for name in sorted(checksums))
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(lines)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return checksums


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--base-model-id", required=True)
    parser.add_argument("--base-revision", required=True)
    parser.add_argument("--thorsten-model-id", required=True)
    parser.add_argument("--thorsten-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        checksums = verify_and_write(
            args.model_dir,
            args.base_model_id,
            args.base_revision,
            args.thorsten_model_id,
            args.thorsten_revision,
            args.output,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed with a concise diagnostic
        print(f"Model revision integrity verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Verified {len(checksums)} model files against immutable Hugging Face revisions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
