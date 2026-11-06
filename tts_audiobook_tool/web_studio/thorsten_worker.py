"""RunPod-only persistent Thorsten CosyVoice3 SFT worker.

The official Thorsten-Voice infer_thorsten.py CLI is the source for the
well-defined SFT frontend patch. Keeping the model warm avoids reloading
several gigabytes of weights for every sentence group.

This process only accepts JSON lines from the local authenticated Studio
parent. It never binds a port, executes a shell or accepts voice references.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys

# The parent starts this worker with cwd set to the installed CosyVoice root.
sys.path.insert(0, os.getcwd())
sys.path.insert(0, str(Path.cwd() / "third_party" / "Matcha-TTS"))


def main() -> None:
    with redirect_stdout(sys.stderr):
        import torch
        import torchaudio
        from cosyvoice.cli.cosyvoice import CosyVoice3

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
        except Exception:
            # Avoid reflecting private audiobook passages or file paths in logs.
            print(json.dumps({"id": request.get("id") if isinstance(request, dict) else None,
                              "ok": False, "error": "TTS-Inferenz fehlgeschlagen."}), flush=True)


if __name__ == "__main__":
    main()
