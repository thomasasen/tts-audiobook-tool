"""RunPod-only persistent Thorsten CosyVoice3 SFT worker.

The official Thorsten-Voice infer_thorsten.py CLI is the source for the
well-defined SFT frontend patch. Keeping the model warm avoids reloading
several gigabytes of weights for every sentence group.

This process only accepts JSON lines from the local authenticated Studio
parent. It never binds a port, executes a shell or accepts voice references.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
import json
import os
from pathlib import Path
import sys

# The parent starts this worker with cwd set to the installed CosyVoice root.
sys.path.insert(0, os.getcwd())
sys.path.insert(0, str(Path.cwd() / "third_party" / "Matcha-TTS"))



@contextmanager
def _cpu_speech_tokenizer_frontend():
    """Initialize only CosyVoice3's reference-audio ONNX tokenizer on CPU.

    Upstream chooses CUDAExecutionProvider whenever torch sees a GPU, but
    ONNX Runtime may fail at cudaSetDevice (CUDA error 46). The fixed Thorsten
    SFT narration doesn't use reference-audio token extraction. Keep the
    PyTorch synthesis model on CUDA and do not edit the pinned CosyVoice tree.
    The process-local override is limited to CosyVoice3 construction and
    restored even if that constructor raises.
    """
    import onnxruntime

    original_session = onnxruntime.InferenceSession

    def session(model_path, *args, **kwargs):
        if (Path(str(model_path)).name == "speech_tokenizer_v3.onnx"
                and kwargs.get("providers") == ["CUDAExecutionProvider"]):
            kwargs["providers"] = ["CPUExecutionProvider"]
            print("Thorsten ONNX speech tokenizer: CPUExecutionProvider; PyTorch synthesis remains on CUDA.", file=sys.stderr)
        return original_session(model_path, *args, **kwargs)

    onnxruntime.InferenceSession = session
    try:
        yield
    finally:
        onnxruntime.InferenceSession = original_session


def _cuda_preflight(torch) -> bool:
    """Require an actual CUDA allocation before loading the speech model.

    Some RunPod hosts report an idle device with nvidia-smi and
    torch.cuda.is_available(), but cudaSetDevice fails with CUDA error 46.
    """
    try:
        if not torch.cuda.is_available():
            raise RuntimeError("Keine CUDA-fähige GPU für PyTorch verfügbar.")
        test_tensor = torch.ones(16, device="cuda:0")
        torch.cuda.synchronize()
        del test_tensor
        return True
    except Exception as exc:
        # Errors may include internal driver details; never echo user text.
        summary = str(exc).splitlines()[0][:240]
        print(
            f"Thorsten CUDA preflight FAILED: {type(exc).__name__}: {summary}. "
            "Check GPU host allocation and run runpod/gpu_preflight.py.",
            file=sys.stderr,
        )
        return False


def main() -> None:
    # Keep all import output off the JSON protocol pipe.
    with redirect_stdout(sys.stderr):
        import torch
        cuda_ready = _cuda_preflight(torch)
    if not cuda_ready:
        print(json.dumps({"ready": False, "error_code": "CUDA_UNAVAILABLE"}), flush=True)
        return

    with redirect_stdout(sys.stderr):
        import torchaudio
        from cosyvoice.cli.cosyvoice import CosyVoice3

        # The optional reference-audio ONNX tokenizer must not take over GPU.
        # This does not change PyTorch/CUDA model placement or SFT voice data.
        with _cpu_speech_tokenizer_frontend():
            model = CosyVoice3("pretrained_models/CosyVoice3-0.5B")
        original_frontend = model.frontend.frontend_sft

        def patched_frontend_sft(tts_text, spk_id):
            payload = original_frontend(tts_text, spk_id)
            prompt_token, prompt_token_len = model.frontend._extract_text_token(
                "You are a helpful assistant.<|endofprompt|>"
            )
            payload["prompt_text"] = prompt_token
            payload["prompt_text_len"] = prompt_token_len
            return payload

        model.frontend.frontend_sft = patched_frontend_sft

    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            output = Path(request["output"])
            text = request["text"]
            if not output.is_absolute() or not isinstance(text, str) or not 1 <= len(text) <= 12000:
                raise ValueError("Invalid generation input")
            with redirect_stdout(sys.stderr), torch.inference_mode():
                chunks = [
                    result["tts_speech"]
                    for result in model.inference_sft(
                        text, "thorsten", stream=False, speed=1.0
                    )
                ]
                if not chunks:
                    raise RuntimeError("Empty TTS output")
                torchaudio.save(str(output), torch.cat(chunks, dim=1), model.sample_rate)
            print(json.dumps({"id": request["id"], "ok": True}), flush=True)
        except Exception as exc:
            # Diagnose in PRIVATE worker logs: error classes and curated
            # categories, never source text, user paths or third-party
            # exception messages (which may echo the audiobook content).
            category = "inference"
            detail = str(exc).lower()
            if "out of memory" in detail or "cuda error" in detail:
                category = "cuda-memory-or-device"
            elif isinstance(exc, KeyError) and "thorsten" in detail:
                category = "speaker-embedding-not-found"
            elif "backend" in detail or "torchaudio" in detail:
                category = "wav-encoding"
            print(
                f"Thorsten worker failure: {type(exc).__name__} ({category})",
                file=sys.stderr,
                flush=True,
            )
            print(json.dumps({"id": request.get("id") if isinstance(request, dict) else None,
                              "ok": False, "error": "TTS-Inferenz fehlgeschlagen."}), flush=True)


if __name__ == "__main__":
    main()
