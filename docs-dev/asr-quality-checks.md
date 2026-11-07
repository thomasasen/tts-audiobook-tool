# German Web Studio: ASR readiness and conservative quality checks (A01)

## Decision

The isolated `web` venv installs `faster-whisper==1.2.1` and
`ctranslate2==4.7.1` **directly** via `web/requirements-runtime.txt`.
Other TTS venvs may already carry faster-whisper transitively; they are **not**
the runtime that executes the Web Studio's CPU checks. `install-web.sh`
verifies both pinned packages from inside its own environment and cannot
mark that environment ready on an import/version failure.

`start-web.sh` defaults to `AUDIOBOOK_STUDIO_ASR_MODEL=small`; operators may
set `off` to skip speech recognition. First use requires a network download
(or pre-populated private model cache). The model is CPU/int8 and no GPU is
initialized in the web process. This is a review aid, not a production gate.

The authenticated `/api/capabilities` response provides
`asr.state` and a privacy-safe German message:

- `disabled`: no ASR requested.
- `invalid`: unsupported model name.
- `missing`: Python package unavailable to the actual web process.
- `not_loaded`: package present; model not yet initialized/downloaded.
- `ready`: model loaded, last ASR attempt succeeded.
- `failed`: last model load or decoding/transcription failed; subsequent
  segments may retry.

`quality_report` counts only `passed` and `review` as actually ASR-checked.
A failed, missing or disabled ASR check has `status=not_checked`, a
`reason`, and is included in the UI's explicit unchecked count. Missing
metadata is **not** treated as passed. Preexisting segment metadata needs no
migration; old `passed/review` results remain visible, and a cached segment
is not regenerated just to obtain new metrics.

## Review metrics and privacy

The intended spoken prompt and recognition are compared with Unicode-aware
word tokens, normalized WER (minimum edit distance), CER and changed alignment
block counts. Explicit differing written digits receive a separate flag.
High WER/CER, empty recognized text, and changed numbers become `review`
suggestions. Even a low WER/CER is **not proof of fidelity**. Human listening
is required for omissions, repetition, pronunciation, prosody and numbers;
ASR itself can be wrong. The thresholds are conservative heuristics, not
validated precision/recall measurements.

No recognized transcript, source book text, private path, package exception or
token is returned by this helper to the browser or persisted in *new* quality
metadata. The private original WAV remains on the RunPod volume. Existing
historical quality JSON files may contain a stored transcript created by older
versions; they are not uploaded to GitHub and are left untouched to preserve
job/recovery compatibility.

## Tests and limitations

- `python -m pytest -q tests/web --confcutdir=tests/web`: status, metrics,
  failure/privacy, persistence/API and existing Web Studio flows.
- `python tests/web/browser_smoke.py`: built React production UI displays
  non-performed checks and their reasons, requiring Playwright/Chromium.
- `python -m runpod.asr_cpu_smoke`: genuine CPU inference on an original,
  synthetically spoken German eSpeak-NG WAV and a missing-file negative case.
  CI pins packages separately and caches only public Whisper-tiny weights.

The CPU smoke exercises `tiny`, not the default `small` weights or any
Thorsten-generated book WAV. It proves real Whisper inference only if that CI
job actually completes successfully. It is **not** a human Hörabnahme, GPU
test, long-form text-fidelity validation, or RunPod restart test. Those remain
explicit release gates in `runpod/ACCEPTANCE.md`.
