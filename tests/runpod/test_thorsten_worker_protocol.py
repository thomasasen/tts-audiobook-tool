"""Hermetic model-less protocol tests for Thorsten's persistent worker.

Exercise the real worker main loop with fake torch/CosyVoice/ONNX modules.
No model weights, CUDA hardware, audio contents or remote services required.
"""
from __future__ import annotations

from contextlib import nullcontext
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest import TestCase, main, mock

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "tts_audiobook_tool/web_studio/thorsten_worker.py"


def load_worker():
    spec = importlib.util.spec_from_file_location("thorsten_worker_protocol_test", WORKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestThorstenProtocol(TestCase):
    def run_worker(self, request, *, gpu_ok=True):
        sessions = []
        models = []
        output = io.StringIO()
        errors = io.StringIO()
        modules = {}
        torch = ModuleType("torch")
        torch.cuda = SimpleNamespace(
            is_available=lambda: True,
            synchronize=lambda: None,
        )

        def ones(n, *, device):
            self.assertEqual((n, device), (16, "cuda:0"))
            if not gpu_ok:
                raise RuntimeError("CUDA error 46: CUDA-capable device(s) busy or unavailable")
            return [1] * n

        torch.ones = ones
        torch.cat = lambda chunks, dim: [1, 2, 3]
        torch.inference_mode = nullcontext
        modules["torch"] = torch

        torchaudio = ModuleType("torchaudio")
        def save(path, data, sample_rate):
            self.assertEqual((data, sample_rate), ([1, 2, 3], 24000))
            Path(path).write_bytes(b"fake-audio")
        torchaudio.save = save
        modules["torchaudio"] = torchaudio

        onnx = ModuleType("onnxruntime")
        def onnx_session(path, *, providers):
            sessions.append((str(path), providers))
            if providers == ["CUDAExecutionProvider"]:
                raise RuntimeError("CUDA error 46: unavailable")
            return object()
        onnx.InferenceSession = onnx_session
        modules["onnxruntime"] = onnx

        cosyvoice_pkg = ModuleType("cosyvoice")
        cosyvoice_pkg.__path__ = []
        cli_pkg = ModuleType("cosyvoice.cli")
        cli_pkg.__path__ = []
        cosyvoice_mod = ModuleType("cosyvoice.cli.cosyvoice")

        class FakeCosyVoice3:
            def __init__(self, model_dir):
                models.append(self)
                self.assert_model_dir = model_dir
                onnx.InferenceSession("speech_tokenizer_v3.onnx", providers=["CUDAExecutionProvider"])
                self.frontend = SimpleNamespace(
                    frontend_sft=lambda text, speaker: {"text": text, "speaker": speaker},
                    _extract_text_token=lambda text: (["token"], 1),
                )
                self.sample_rate = 24000

            def inference_sft(self, text, speaker, **options):
                self.last_request = (text, speaker, options)
                self.patch_input = self.frontend.frontend_sft(text, speaker)
                yield {"tts_speech": object()}

        cosyvoice_mod.CosyVoice3 = FakeCosyVoice3
        modules.update({
            "cosyvoice": cosyvoice_pkg,
            "cosyvoice.cli": cli_pkg,
            "cosyvoice.cli.cosyvoice": cosyvoice_mod,
        })
        worker = load_worker()
        with mock.patch.dict(sys.modules, modules):
            with mock.patch.object(sys, "stdin", io.StringIO(request)):
                with mock.patch.object(sys, "stdout", output):
                    with mock.patch.object(sys, "stderr", errors):
                        worker.main()
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        return messages, errors.getvalue(), sessions, models

    def test_worker_ready_protocol_cpu_onnx_and_audio_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            audio = Path(temp) / "voice.wav"
            message = json.dumps({
                "id": "synthetic-request-1",
                "text": "Guten Tag, das ist ein Test.",
                "output": str(audio),
            }, ensure_ascii=False) + "\n"
            messages, errors, sessions, models = self.run_worker(message)
            self.assertEqual(messages, [
                {"ready": True},
                {"id": "synthetic-request-1", "ok": True},
            ])
            self.assertTrue(audio.is_file())
            self.assertEqual(audio.read_bytes(), b"fake-audio")
            self.assertEqual(sessions, [("speech_tokenizer_v3.onnx", ["CPUExecutionProvider"])])
            self.assertEqual(models[0].last_request[1], "thorsten")
            self.assertEqual(models[0].last_request[2], {"stream": False, "speed": 1.0})
            self.assertEqual(models[0].patch_input["prompt_text"], ["token"])
            self.assertEqual(models[0].patch_input["prompt_text_len"], 1)
            self.assertNotIn("Guten Tag", errors)

    def test_cuda_error_46_reports_structured_not_ready_before_model_load(self):
        messages, errors, sessions, models = self.run_worker("", gpu_ok=False)
        self.assertEqual(messages, [{"ready": False, "error_code": "CUDA_UNAVAILABLE"}])
        self.assertIn("CUDA error 46", errors)
        self.assertEqual(sessions, [])
        self.assertEqual(models, [])

    def test_malformed_worker_input_never_reflects_private_text(self):
        private_text = "Privater Buchabsatz 73939"
        messages, errors, sessions, models = self.run_worker(
            json.dumps({"id": "bad", "text": private_text, "output": "relative.wav"}) + "\n"
        )
        self.assertEqual(messages[0], {"ready": True})
        self.assertEqual(messages[1]["id"], "bad")
        self.assertFalse(messages[1]["ok"])
        self.assertEqual(messages[1]["error"], "TTS-Inferenz fehlgeschlagen.")
        self.assertIn("Thorsten worker failure: ValueError (inference)", errors)
        self.assertNotIn(private_text, str(messages) + errors)



class TestStudioWorkerIntegration(TestCase):
    def load_production(self):
        path = ROOT / "tts_audiobook_tool/web_studio/production.py"
        spec = importlib.util.spec_from_file_location("production_protocol_test", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_parent_distinguishes_cuda_unavailable_from_model_failure(self):
        production = self.load_production()
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "infer_thorsten.py"
            model.write_text("# synthetic model path\\n")
            worker = SimpleNamespace(
                poll=lambda: None,
                terminate=mock.Mock(),
                wait=mock.Mock(),
                stdin=io.StringIO(),
                stdout=io.StringIO(),
            )
            studio = SimpleNamespace(data_dir=lambda: Path(tmp))
            with mock.patch.object(production, "_studio", return_value=studio), \
                 mock.patch.object(production.subprocess, "Popen", return_value=worker), \
                 mock.patch.object(production, "_model_message", return_value={
                     "ready": False, "error_code": "CUDA_UNAVAILABLE",
                 }):
                with self.assertRaisesRegex(RuntimeError, "GPU nicht für CUDA-Ausführung verfügbar"):
                    production._generate_with_worker(
                        "German sample", Path(tmp) / "voice.wav",
                        (sys.executable, str(model)),
                    )
            worker.terminate.assert_called_once()
            self.assertIsNone(production._MODEL_PROC)

    def test_parent_sends_id_and_private_text_over_pipe_only(self):
        production = self.load_production()
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "infer_thorsten.py"
            model.write_text("# synthetic model path\\n")
            worker = SimpleNamespace(
                poll=lambda: None,
                terminate=mock.Mock(),
                wait=mock.Mock(),
                stdin=io.StringIO(),
                stdout=io.StringIO(),
            )
            messages = iter([
                {"ready": True},
                {"id": "synthetic-request-id", "ok": True},
            ])
            studio = SimpleNamespace(data_dir=lambda: Path(tmp))
            uid = SimpleNamespace(hex="synthetic-request-id")
            with mock.patch.object(production, "_studio", return_value=studio), \
                 mock.patch.object(production.subprocess, "Popen", return_value=worker), \
                 mock.patch.object(production, "_model_message", side_effect=lambda *args: next(messages)), \
                 mock.patch.object(production.uuid, "uuid4", return_value=uid):
                production._generate_with_worker(
                    "Synthetic private book passage", Path(tmp) / "voice.wav",
                    (sys.executable, str(model)),
                )
            request = json.loads(worker.stdin.getvalue().strip())
            self.assertEqual(request["id"], "synthetic-request-id")
            self.assertEqual(request["text"], "Synthetic private book passage")
            self.assertEqual(request["output"], str(Path(tmp) / "voice.wav"))
            # Do not leak the model session into other tests.
            production._MODEL_PROC = None

if __name__ == "__main__":
    main()
