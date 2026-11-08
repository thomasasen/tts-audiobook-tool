#!/usr/bin/env python3
"""RunPod GPU execution preflight; no model downloads and no audiobook data access.

NVML/nvidia-smi can report a visible idle GPU even when CUDA operations fail.
This probe validates a real CUDA allocation and synchronization in the isolated
Thorsten and/or Qwen runtimes. It never changes files or calls model inference.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

CUDA_CODE = """
import json
import torch
result = {
    "torch": torch.__version__,
    "torch_cuda": torch.version.cuda,
    "cuda_visible": bool(torch.cuda.is_available()),
}
try:
    # is_available() only checks visibility; the actual device may be unusable.
    torch.cuda.init()
    x = torch.ones(16, device="cuda:0")
    torch.cuda.synchronize()
    result["device"] = torch.cuda.get_device_name(0)
    result["compute_capability"] = list(torch.cuda.get_device_capability(0))
    result["cuda_operational"] = True
except Exception as exc:
    result["cuda_operational"] = False
    result["error"] = type(exc).__name__ + ": " + str(exc).splitlines()[0][:250]
print(json.dumps(result))
raise SystemExit(0 if result["cuda_operational"] else 1)
"""


def check_runtime(name: str, root: Path, *, timeout: int = 50) -> dict:
    # Use the official PyTorch Pod image's system Python for a cheap CUDA
    # execution gate BEFORE any giant model dependency or weight download.
    executable = Path(sys.executable) if name == "system" else root / "venvs" / name / "bin" / "python"
    if not executable.is_file():
        return {"name": name, "cuda_operational": False, "reason": "runtime not installed"}
    try:
        process = subprocess.run(
            [str(executable), "-c", CUDA_CODE],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"name": name, "cuda_operational": False, "reason": "CUDA probe timed out"}
    except OSError:
        return {"name": name, "cuda_operational": False, "reason": "runtime not executable"}
    try:
        # Defensive: torch may emit logs before the JSON line. Never echo arbitrary
        # stderr into Pod logs, as it may contain user-specific file paths.
        lines = process.stdout.strip().splitlines()
        data = json.loads(lines[-1])
        if not isinstance(data, dict) or not isinstance(data.get("cuda_operational"), bool):
            raise ValueError("invalid JSON schema")
    except (IndexError, TypeError, ValueError, json.JSONDecodeError):
        return {
            "name": name, "cuda_operational": False,
            "reason": "CUDA probe produced no valid JSON; see private runtime logs",
            "return_code": process.returncode,
        }
    data["name"] = name
    # A broken runtime must never be reported ready, even if it prints valid JSON.
    data["cuda_operational"] = bool(data["cuda_operational"] and process.returncode == 0)
    return data


def inspect(root: Path, names: tuple[str, ...]) -> dict:
    try:
        device = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=12, check=False,
        )
        smi_ok = device.returncode == 0
        gpu_name = device.stdout.splitlines()[0].split(",")[0].strip() if smi_ok and device.stdout.strip() else None
    except (OSError, subprocess.TimeoutExpired):
        smi_ok, gpu_name = False, None
    runtimes = [check_runtime(name, root) for name in names]
    return {
        "gpu_visible_via_nvidia_smi": smi_ok,
        "gpu_name": gpu_name,
        "runtimes": runtimes,
        "ready": bool(runtimes) and all(r["cuda_operational"] for r in runtimes),
    }

# The native Thorsten dependency lock intentionally uses torch 2.3.1+cu121.
# That release predates NVIDIA Blackwell (sm_100/sm_120). The *system* PyTorch
# in the RunPod image may run successfully on Blackwell, which otherwise gives
# a misleading green CUDA preflight before expensive model downloads.
THORSTEN_LOCK = Path(__file__).resolve().parent / "locks" / "thorsten" / "pyproject.toml"


def check_thorsten_gpu_compatibility(report: dict) -> dict:
    """Conservatively gate Blackwell until the pinned native TTS stack is upgraded."""
    report["thorsten_compatible"] = False
    if not report.get("ready"):
        report["thorsten_compatibility_error"] = "System CUDA execution failed; compatibility not proven."
        return report
    try:
        pinned_dependencies = THORSTEN_LOCK.read_text(encoding="utf-8")
    except OSError:
        pinned_dependencies = ""
    # Explicitly couple the policy to the current lock; changing the pin must
    # trigger a new compatibility review, not silently retain an obsolete rule.
    if '"torch==2.3.1"' not in pinned_dependencies:
        report["ready"] = False
        report["thorsten_compatibility_error"] = (
            "Thorsten PyTorch pin is not the audited 2.3.1; GPU compatibility must be reviewed."
        )
        return report
    runtime = next((r for r in report["runtimes"] if r.get("name") == "system"), {})
    capability = runtime.get("compute_capability")
    if (not isinstance(capability, list) or len(capability) != 2
            or any(type(v) is not int or v < 0 for v in capability)):
        report["ready"] = False
        report["thorsten_compatibility_error"] = (
            "GPU compute capability was not reported by system PyTorch; refusing an unverified download."
        )
        return report
    major, minor = capability
    report["thorsten_gpu_compute_capability"] = f"sm_{major}{minor}"
    if major >= 10:
        report["ready"] = False
        report["thorsten_compatibility_error"] = (
            f"GPU sm_{major}{minor} is not supported by the pinned Thorsten "
            "PyTorch 2.3.1+cu121 runtime. RTX 5090/other Blackwell cards "
            "require a separately validated newer TTS runtime. "
            "Use RTX 4090, L40S, L4, RTX 3090 or A100 instead."
        )
        return report
    report["thorsten_compatible"] = True
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=("system", "qwen", "thorsten", "both"), default="both")
    parser.add_argument("--json", action="store_true", help="print concise JSON diagnostic")
    parser.add_argument(
        "--require-thorsten-compatible", action="store_true",
        help="System preflight also rejects GPU architectures unsupported by pinned Thorsten torch 2.3.1",
    )
    args = parser.parse_args()
    if args.require_thorsten_compatible and args.env != "system":
        parser.error("--require-thorsten-compatible requires --env system")
    root = Path(os.environ.get("AUDIOBOOK_DATA_ROOT",
                               str(Path(os.environ.get("RUNPOD_WORKSPACE", "/workspace")) / "audiobook-data")))
    names = ("qwen", "thorsten") if args.env == "both" else (args.env,)
    report = inspect(root, names)
    if args.require_thorsten_compatible:
        report = check_thorsten_gpu_compatibility(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print("RunPod CUDA execution preflight (actual memory allocation)")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if not report["ready"]:
            print(report.get("thorsten_compatibility_error") or
                  "CUDA NICHT BETRIEBSBEREIT: GPU-Zuweisung/Host prüfen; "
                  "kein Modell- oder setuptools-Problem nachgewiesen.",
                  file=sys.stderr)
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
