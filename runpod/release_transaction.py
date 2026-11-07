#!/usr/bin/env python3
"""Atomic backup and rollback for persistent RunPod audiobook release assets."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
BACKUP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CHUNK_SIZE = 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _asset_paths(data_root: Path, venv_root: Path) -> dict[str, Path]:
    return {
        "models/CosyVoice": data_root / "models" / "CosyVoice",
        "models/thorsten-model-sha256.txt": data_root / "models" / "thorsten-model-sha256.txt",
        "models/thorsten-runtime-sha256.txt": data_root / "models" / "thorsten-runtime-sha256.txt",
        "tools/uv-bootstrap": data_root / "tools" / "uv-bootstrap",
        "venvs/thorsten": venv_root / "thorsten",
        "venvs/web": venv_root / "web",
        "venvs/qwen": venv_root / "qwen",
        "venvs/moss": venv_root / "moss",
    }


def _repo_commit(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256_manifest(root: Path, manifest: Path) -> None:
    if not manifest.is_file():
        raise RuntimeError(f"Release checksum manifest is missing: {manifest.name}")
    lines = [line for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"Release checksum manifest is empty: {manifest.name}")
    for line in lines:
        parts = line.split(None, 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise RuntimeError(f"Malformed checksum manifest entry in {manifest.name}")
        filename = parts[1].lstrip("*")
        relative = Path(filename)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"Unsafe checksum path in {manifest.name}")
        target = root / relative
        if not target.is_file() or _sha256(target) != parts[0]:
            raise RuntimeError(f"Release file failed SHA-256 verification: {filename}")


def runtime_freeze_hash(uv: Path, python: Path) -> str:
    result = subprocess.run(
        [str(uv), "pip", "freeze", "--python", str(python)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = sorted(line.rstrip("\r\n") for line in result.stdout.splitlines() if line.strip())
    payload = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
    return hashlib.sha256(payload).hexdigest()


def verify_active_release(data_root: Path, venv_root: Path) -> None:
    paths = _asset_paths(data_root, venv_root)
    uv = data_root / "tools" / "uv-bootstrap" / "bin" / "uv"
    model_root = paths["models/CosyVoice"]
    model_manifest = paths["models/thorsten-model-sha256.txt"]
    thorsten_runtime_manifest = paths["models/thorsten-runtime-sha256.txt"]
    thorsten_python = paths["venvs/thorsten"] / "bin" / "python"

    if _exists(model_root):
        if not (model_root / ".studio-ready").is_file():
            raise RuntimeError("Thorsten model directory exists without a release-ready marker")
        if not _exists(paths["venvs/thorsten"]) or not thorsten_python.is_file():
            raise RuntimeError("Thorsten model is present but its locked runtime is missing")
        verify_sha256_manifest(model_root / "pretrained_models" / "CosyVoice3-0.5B", model_manifest)
        if not thorsten_runtime_manifest.is_file() or not uv.is_file():
            raise RuntimeError("Thorsten runtime fingerprint or pinned uv binary is missing")
        if runtime_freeze_hash(uv, thorsten_python) != _read_text(thorsten_runtime_manifest):
            raise RuntimeError("Thorsten installed packages differ from the recorded runtime fingerprint")
    elif _exists(paths["venvs/thorsten"]):
        raise RuntimeError("Thorsten venv exists without its verified model release")

    if any(_exists(paths[key]) for key in ("venvs/web", "venvs/qwen", "venvs/moss")) and not uv.is_file():
        raise RuntimeError("Pinned uv binary is missing while managed runtime environments exist")
    for name, marker, manifest in (
        ("web", ".studio-web-runtime-ready", ".studio-web-runtime-packages.sha256"),
        ("qwen", ".runpod-ready", ".runpod-packages.sha256"),
        ("moss", ".runpod-ready", ".runpod-packages.sha256"),
    ):
        env_dir = paths[f"venvs/{name}"]
        if not _exists(env_dir):
            continue
        python = env_dir / "bin" / "python"
        marker_path = env_dir / marker
        manifest_path = env_dir / manifest
        if not python.is_file() or not marker_path.is_file() or not manifest_path.is_file():
            raise RuntimeError(f"{name} environment is incomplete or lacks its release fingerprint")
        if runtime_freeze_hash(uv, python) != _read_text(manifest_path):
            raise RuntimeError(f"{name} installed packages differ from the recorded runtime fingerprint")


def _backup_root(data_root: Path) -> Path:
    return data_root / "releases" / "tts-audiobook-tool"


def _validate_backup_id(backup_id: str) -> None:
    if not BACKUP_ID_RE.fullmatch(backup_id) or backup_id in {".", ".."}:
        raise ValueError("Invalid backup ID")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _copy_repo_assets(repo_root: Path, backup_dir: Path) -> dict[str, bool]:
    repo_assets = backup_dir / "repo"
    repo_assets.mkdir(parents=True, exist_ok=True)
    dist = repo_root / "web" / "dist"
    pins = repo_root / "runpod" / "model-pins.env"
    dist_present = dist.is_dir()
    pins_present = pins.is_file()
    if dist_present:
        shutil.copytree(dist, repo_assets / "web-dist", symlinks=True)
    if pins_present:
        shutil.copy2(pins, repo_assets / "model-pins.env")
    return {"web_dist": dist_present, "model_pins": pins_present}


def _backup_active(
    data_root: Path,
    venv_root: Path,
    repo_root: Path,
    backup_id: str,
    verify: bool,
) -> Path:
    _validate_backup_id(backup_id)
    data_root = data_root.resolve()
    venv_root = venv_root.resolve()
    repo_root = repo_root.resolve()
    backup_root = _backup_root(data_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_dir = backup_root / backup_id
    if _exists(backup_dir):
        raise FileExistsError(f"Release backup already exists: {backup_id}")
    if data_root.stat().st_dev != venv_root.stat().st_dev:
        raise RuntimeError("AUDIOBOOK_DATA_ROOT and AUDIOBOOK_VENV_DIR must share a filesystem for atomic release rollback")
    if verify:
        verify_active_release(data_root, venv_root)

    paths = _asset_paths(data_root, venv_root)
    markers = {
        "thorsten": _read_text(paths["models/CosyVoice"] / ".studio-ready"),
        "web": _read_text(paths["venvs/web"] / ".studio-web-runtime-ready"),
        "qwen": _read_text(paths["venvs/qwen"] / ".runpod-ready"),
        "moss": _read_text(paths["venvs/moss"] / ".runpod-ready"),
    }
    backup_dir.mkdir(parents=True)
    moved: list[tuple[Path, Path]] = []
    try:
        assets: dict[str, dict[str, Any]] = {}
        for key, source in paths.items():
            present = _exists(source)
            assets[key] = {"present": present, "is_symlink": source.is_symlink() if present else False}
            if not present:
                continue
            if source.is_symlink():
                raise RuntimeError(f"Refusing to back up a symlinked release root: {key}")
            destination = backup_dir / "assets" / key
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.stat().st_dev != backup_dir.stat().st_dev:
                raise RuntimeError(f"Release asset is not on the persistent backup filesystem: {key}")
            os.replace(source, destination)
            moved.append((destination, source))
        repo_assets = _copy_repo_assets(repo_root, backup_dir)
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "backup_id": backup_id,
            "state": "available",
            "created_at": utc_now(),
            "repo_commit": _repo_commit(repo_root),
            "markers": markers,
            "assets": assets,
            "repo_assets": repo_assets,
        }
        _atomic_json(backup_dir / "metadata.json", metadata)
        (backup_dir / "BACKUP_COMPLETE").write_text("verified\n" if verify else "snapshot\n", encoding="utf-8")
        return backup_dir
    except Exception:
        for destination, source in reversed(moved):
            if _exists(destination) and not _exists(source):
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination, source)
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise


def backup_release(data_root: Path, venv_root: Path, repo_root: Path, backup_id: str) -> Path:
    return _backup_active(data_root, venv_root, repo_root, backup_id, verify=True)


def _load_backup(data_root: Path, backup_id: str) -> tuple[Path, dict[str, Any]]:
    backup_root = _backup_root(data_root)
    if backup_id == "latest":
        candidates = []
        if backup_root.is_dir():
            for path in backup_root.iterdir():
                metadata_path = path / "metadata.json"
                if path.is_dir() and metadata_path.is_file() and (path / "BACKUP_COMPLETE").is_file():
                    try:
                        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    if metadata.get("state") == "available":
                        candidates.append((str(metadata.get("created_at", "")), path, metadata))
        if not candidates:
            raise FileNotFoundError("No available release backup exists")
        _, path, metadata = sorted(candidates, key=lambda item: item[0])[-1]
        return path, metadata
    _validate_backup_id(backup_id)
    backup_dir = backup_root / backup_id
    if not (backup_dir / "BACKUP_COMPLETE").is_file():
        raise FileNotFoundError(f"Release backup is incomplete or missing: {backup_id}")
    metadata = json.loads((backup_dir / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("schema_version") != SCHEMA_VERSION or metadata.get("state") != "available":
        raise RuntimeError("Release backup schema or state is not restorable")
    return backup_dir, metadata


def _restore_assets_only(backup_dir: Path, data_root: Path, venv_root: Path, metadata: dict[str, Any]) -> None:
    paths = _asset_paths(data_root, venv_root)
    if set(metadata.get("assets", {})) != set(paths):
        raise RuntimeError("Backup asset manifest does not match the supported release layout")
    for key, details in metadata["assets"].items():
        if not details.get("present"):
            continue
        source = backup_dir / "assets" / key
        destination = paths[key]
        if not _exists(source) or source.is_symlink():
            raise RuntimeError(f"Backup asset is missing or unsafe: {key}")
        if _exists(destination):
            raise FileExistsError(f"Refusing to overwrite active release asset: {key}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)


def _restore_repo_dist(backup_dir: Path, repo_root: Path, metadata: dict[str, Any]) -> None:
    destination = repo_root / "web" / "dist"
    source = backup_dir / "repo" / "web-dist"
    if _exists(destination):
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    if metadata.get("repo_assets", {}).get("web_dist"):
        if not source.is_dir():
            raise RuntimeError("Backup Web Studio distribution is missing")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, symlinks=True)


def restore_release(
    data_root: Path,
    venv_root: Path,
    repo_root: Path,
    backup_id: str,
    allow_code_mismatch: bool = False,
) -> str:
    data_root = data_root.resolve()
    venv_root = venv_root.resolve()
    repo_root = repo_root.resolve()
    target_dir, target_metadata = _load_backup(data_root, backup_id)
    current_commit = _repo_commit(repo_root)
    target_commit = target_metadata.get("repo_commit")
    if current_commit and target_commit and current_commit != target_commit and not allow_code_mismatch:
        raise RuntimeError(
            f"Application code is {current_commit}, but backup used {target_commit}. "
            "Use --allow-code-mismatch only if you accept restoring runtime assets under different code."
        )

    pre_id = "pre-rollback-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    pre_dir = _backup_active(data_root, venv_root, repo_root, pre_id, verify=False)
    pre_metadata = json.loads((pre_dir / "metadata.json").read_text(encoding="utf-8"))
    moved_from_target: list[tuple[Path, Path]] = []
    try:
        paths = _asset_paths(data_root, venv_root)
        if set(target_metadata.get("assets", {})) != set(paths):
            raise RuntimeError("Backup asset manifest does not match the supported release layout")
        for key, details in target_metadata["assets"].items():
            if not details.get("present"):
                continue
            source = target_dir / "assets" / key
            destination = paths[key]
            if not _exists(source) or source.is_symlink():
                raise RuntimeError(f"Backup asset is missing or unsafe: {key}")
            if _exists(destination):
                raise FileExistsError(f"Active path unexpectedly exists during rollback: {key}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
            moved_from_target.append((destination, source))
        _restore_repo_dist(target_dir, repo_root, target_metadata)
        target_metadata["state"] = "restored"
        target_metadata["restored_at"] = utc_now()
        target_metadata["pre_rollback_backup_id"] = pre_id
        _atomic_json(target_dir / "metadata.json", target_metadata)
        return pre_id
    except Exception:
        for destination, source in reversed(moved_from_target):
            if _exists(destination) and not _exists(source):
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination, source)
        _restore_assets_only(pre_dir, data_root, venv_root, pre_metadata)
        _restore_repo_dist(pre_dir, repo_root, pre_metadata)
        pre_metadata["state"] = "restored"
        pre_metadata["restored_at"] = utc_now()
        _atomic_json(pre_dir / "metadata.json", pre_metadata)
        raise


def list_backups(data_root: Path) -> list[dict[str, Any]]:
    backup_root = _backup_root(data_root)
    result = []
    if not backup_root.is_dir():
        return result
    for path in sorted(backup_root.iterdir()):
        metadata_path = path / "metadata.json"
        if not path.is_dir() or not metadata_path.is_file():
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        result.append({
            "backup_id": metadata.get("backup_id", path.name),
            "state": metadata.get("state"),
            "created_at": metadata.get("created_at"),
            "repo_commit": metadata.get("repo_commit"),
            "path": str(path),
        })
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup")
    restore_parser = subparsers.add_parser("restore")
    list_parser = subparsers.add_parser("list")
    for item in (backup_parser, restore_parser):
        item.add_argument("--data-root", type=Path, required=True)
        item.add_argument("--venv-root", type=Path, required=True)
        item.add_argument("--repo-root", type=Path, required=True)
    backup_parser.add_argument("--backup-id", required=True)
    restore_parser.add_argument("--backup-id", required=True)
    restore_parser.add_argument("--confirm", action="store_true")
    restore_parser.add_argument("--allow-code-mismatch", action="store_true")
    list_parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            backup_dir = backup_release(args.data_root, args.venv_root, args.repo_root, args.backup_id)
            print(json.dumps({"backup_id": args.backup_id, "backup_dir": str(backup_dir)}))
        elif args.command == "restore":
            if not args.confirm:
                raise RuntimeError("Rollback is destructive; pass --confirm explicitly")
            pre_id = restore_release(
                args.data_root, args.venv_root, args.repo_root,
                args.backup_id, allow_code_mismatch=args.allow_code_mismatch,
            )
            _, restored_metadata = _load_backup(args.data_root.resolve(), args.backup_id)
            print(json.dumps({
                "restored_backup": args.backup_id,
                "pre_rollback_backup": pre_id,
                "previous_repo_commit": restored_metadata.get("repo_commit"),
            }))
        else:
            print(json.dumps(list_backups(args.data_root), indent=2))
    except Exception as exc:  # noqa: BLE001 - CLI must fail closed and report one concise reason
        print(f"Release transaction failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
