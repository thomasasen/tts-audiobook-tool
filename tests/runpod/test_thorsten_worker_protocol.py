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
        def save(path, data, sample_rate, **kwargs):
            self.assertEqual((data, sample_rate), ([1, 2, 3], 24000))
            self.assertEqual(kwargs, {
                "format": "wav", "encoding": "PCM_S", "bits_per_sample": 16,
            })
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
                self.tokenizations = []
                onnx.InferenceSession("speech_tokenizer_v3.onnx", providers=["CUDAExecutionProvider"])

                def extract(text):
                    self.tokenizations.append(text)
                    return ["token"], 1

                self.frontend = SimpleNamespace(
                    frontend_sft=lambda text, speaker: {"text": text, "speaker": speaker},
                    _extract_text_token=extract,
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

    def test_fixed_prompt_tokenized_once_across_multiple_segments(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = [str(Path(temp) / f"{i}.wav") for i in (1, 2)]
            requests = "".join(json.dumps({
                "id": str(i), "text": f"Fachtext {i}", "output": output,
            }) + "\n" for i, output in enumerate(paths, start=1))
            messages, _, _, models = self.run_worker(requests)
            self.assertEqual(messages, [
                {"ready": True}, {"id": "1", "ok": True}, {"id": "2", "ok": True},
            ])
            self.assertEqual(models[0].tokenizations, [
                "You are a helpful assistant.<|endofprompt|>"
            ])
            self.assertTrue(all(Path(path).is_file() for path in paths))

    def test_benchmark_uncached_prompt_reproduces_old_path_without_affecting_default(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = [str(Path(temp) / f"{i}.wav") for i in (1, 2, 3)]
            requests = "".join(json.dumps({
                "id": str(i), "text": f"Fachtext {i}", "output": output,
                "benchmark": True,
                "uncached_fixed_prompt": i in (1, 2),
            }) + "\n" for i, output in enumerate(paths, start=1))
            messages, _, _, models = self.run_worker(requests)
            self.assertEqual([item.get("ok") for item in messages[1:]], [True] * 3)
            self.assertEqual(len(models), 1)
            # One initial cached tokenization plus two benchmark-only legacy calls.
            self.assertEqual(len(models[0].tokenizations), 3)
            for result in messages[1:]:
                self.assertGreaterEqual(result["metrics"]["inference_seconds"], 0)
                self.assertGreaterEqual(result["metrics"]["wav_seconds"], 0)
            self.assertEqual(
                [item["id"] for item in messages[1:]], ["1", "2", "3"]
            )

    def test_official_thorsten_speed_passes_to_real_sft_entrypoint(self):
        with tempfile.TemporaryDirectory() as temp:
            audio = Path(temp) / "different-tempo.wav"
            msg = json.dumps({
                "id": "speed-1-10", "text": "Das ist ein kontrollierter Hörbuchtest.",
                "output": str(audio), "speed": 1.1,
            }) + "\n"
            messages, _, _, models = self.run_worker(msg)
            self.assertEqual(messages[0], {"ready": True})
            self.assertTrue(messages[1]["ok"])
            self.assertEqual(models[0].last_request[2]["speed"], 1.1)
            self.assertTrue(audio.is_file())

    def test_invalid_sft_speed_rejected_before_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            audio = Path(temp) / "not-created.wav"
            msg = json.dumps({
                "id": "invalid-speed", "text": "Das darf nicht gesprochen werden.",
                "output": str(audio), "speed": 1.37,
            }) + "\n"
            messages, errors, _, models = self.run_worker(msg)
            self.assertEqual(messages[0], {"ready": True})
            self.assertFalse(messages[1]["ok"])
            self.assertFalse(audio.exists())
            self.assertFalse(hasattr(models[0], "last_request"))
            self.assertNotIn("Das darf nicht", errors)

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

    def test_idle_preload_warms_one_worker_reused_by_all_segments(self):
        production = self.load_production()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "infer_thorsten.py"
            script.write_text("# synthetic model path\n")
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
                {"id": "synthetic-request-id", "ok": True},
            ])
            studio = SimpleNamespace(data_dir=lambda: root)
            uid = SimpleNamespace(hex="synthetic-request-id")
            config = (sys.executable, str(script))
            with mock.patch.dict("os.environ", {"AUDIOBOOK_STUDIO_PRELOAD_VOICE": "1"}), \
                 mock.patch.object(production, "_studio", return_value=studio), \
                 mock.patch.object(production, "_config", return_value=config), \
                 mock.patch.object(production, "_model_identity", return_value="valid-installed-model"), \
                 mock.patch.object(production.subprocess, "Popen", return_value=worker) as spawn, \
                 mock.patch.object(production, "_model_message", side_effect=lambda *args: next(messages)), \
                 mock.patch.object(production.uuid, "uuid4", return_value=uid):
                production._STOP.clear()
                production._WARMUP_ATTEMPTED = False
                production._WARMUP_STATE = "waiting"
                production._prewarm_idle_voice()
                self.assertTrue(production._model_loaded())
                self.assertEqual(production.voice_warmup_state(), "ready")
                self.assertTrue(production._WARMUP_ATTEMPTED)
                self.assertFalse(production._WARMUP_ACTIVE.is_set())
                production._generate_with_worker("Sentence one", root / "a.wav", config)
                production._generate_with_worker("Sentence two", root / "b.wav", config)
                production._prewarm_idle_voice()
                spawn.assert_called_once()
                self.assertEqual(len(worker.stdin.getvalue().splitlines()), 2)
                production._close_model()
                self.assertFalse(production._model_loaded())

    def test_failed_preload_does_not_retry_endlessly_on_paid_gpu(self):
        production = self.load_production()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "infer_thorsten.py"
            script.write_text("# synthetic model path\n")
            worker = SimpleNamespace(
                poll=lambda: None,
                terminate=mock.Mock(),
                wait=mock.Mock(),
                stdin=io.StringIO(),
                stdout=io.StringIO(),
            )
            config = (sys.executable, str(script))
            with mock.patch.dict("os.environ", {"AUDIOBOOK_STUDIO_PRELOAD_VOICE": "1"}), \
                 mock.patch.object(production, "_studio", return_value=SimpleNamespace(data_dir=lambda: root)), \
                 mock.patch.object(production, "_config", return_value=config), \
                 mock.patch.object(production, "_model_identity", return_value="valid-installed-model"), \
                 mock.patch.object(production.subprocess, "Popen", return_value=worker) as spawn, \
                 mock.patch.object(production, "_model_message", return_value={"ready": False}):
                production._STOP.clear()
                production._WARMUP_ATTEMPTED = False
                production._WARMUP_STATE = "waiting"
                production._prewarm_idle_voice()
                self.assertEqual(production.voice_warmup_state(), "failed")
                self.assertIsNone(production._MODEL_PROC)
                production._prewarm_idle_voice()
                spawn.assert_called_once()
                worker.terminate.assert_called_once()

    def test_preload_can_be_disabled_or_defers_when_gpu_busy(self):
        production = self.load_production()
        with tempfile.TemporaryDirectory() as tmp:
            config = (sys.executable, str(Path(tmp) / "infer_thorsten.py"))
            with mock.patch.object(production, "_config", return_value=config), \
                 mock.patch.object(production, "_model_identity", return_value="model"), \
                 mock.patch.object(production, "_ensure_model_loaded") as load:
                production._STOP.clear()
                production._WARMUP_ATTEMPTED = False
                with mock.patch.dict("os.environ", {"AUDIOBOOK_STUDIO_PRELOAD_VOICE": "0"}):
                    production._prewarm_idle_voice()
                    self.assertEqual(production.voice_warmup_state(), "disabled")
                self.assertFalse(production._WARMUP_ATTEMPTED)
                acquired = production._GPU.acquire(blocking=False)
                self.assertTrue(acquired)
                try:
                    with mock.patch.dict("os.environ", {"AUDIOBOOK_STUDIO_PRELOAD_VOICE": "1"}):
                        production._prewarm_idle_voice()
                finally:
                    production._GPU.release()
                self.assertFalse(production._WARMUP_ATTEMPTED)
                load.assert_not_called()

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
            self.assertEqual(request["speed"], 1.0)
            # Do not leak the model session into other tests.
            production._MODEL_PROC = None

if __name__ == "__main__":
    main()
