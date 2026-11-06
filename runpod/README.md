# RunPod deployment for German nonfiction audiobooks (P0)

Status: **scripts and static checks only**. Real GPU model loading, German
speech generation and measured VRAM/time/cost still require a RunPod Pod.
The existing application is unchanged; this is an opt-in deployment layer.

This setup follows the existing `launch.py` launcher and the upstream model
requirements. Qwen3-TTS and MOSS-TTS need different Torch versions and **must
not** share a virtual environment. The GPU driver must support the CUDA
runtime installed by the pinned Torch packages.

## 1. Provision the Pod

1. In the RunPod dashboard, create a **GPU Pod** using the official **PyTorch
   2.8 / CUDA 12.8** (Ubuntu) template. Follow the current template selector
   rather than assuming an old Docker tag is still offered.
2. Use an NVIDIA GPU with approximately **48 GB VRAM** for initial Qwen/MOSS
   comparison. A lower-VRAM GPU may run Qwen, but first test actual model
   loading and available headroom; this is not a verified minimum.
3. Create/attach a **network volume** at `/workspace` *before* deploying
   the Pod. Recommend **150 GB or more** if keeping both separate environments,
   model caches, audio projects and quality-control assets. Monitor remaining
   space as downloads accumulate.
4. Allow **at least 35 GB of container disk** for the base image, apt tools
   and temporary work; increase if this particular template requires it.
   Container storage is disposable. The network volume survives Pod
   termination, subject to RunPod account and billing policies.
5. Connect using the Pod's RunPod Web Terminal or SSH. No public HTTP endpoint
   is needed; the upstream application is a text/terminal UI.

RunPod storage documentation:
https://docs.runpod.io/pods/storage/types

## 2. Install (one-time, and safe to repeat after Pod restarts)

From the terminal:

```bash
cd /workspace
git clone https://github.com/thomasasen/tts-audiobook-tool.git
cd tts-audiobook-tool
git switch feature/runpod-p0

# Optional preflight, no installations or downloaded models:
bash runpod/setup.sh --check-only

# Install both model environments (Python 3.12) on the mounted volume:
bash runpod/setup.sh --models both
```

The script checks that `/workspace` is mounted, verifies an NVIDIA GPU,
installs missing Ubuntu packages, obtains pinned `uv==0.9.11`, installs
managed Python 3.12 **on the network volume**, creates two independent venvs,
and installs each model's upstream pinned requirements. Optional FlashAttention
is not installed: the app falls back to supported attention implementations.
It prints its log path and performs a post-install diagnostic.

For a smaller first test, use `--models qwen` and install MOSS later using
`--models moss`. Running the same command again checks a fingerprint of the
requirements and Python/Torch version; already completed venvs are reused.
If an installation is interrupted, rerun the command. **Do not remove the
volume or terminate the Pod before confirming your files are on the network
volume.**

Persistent directory layout:

```text
/workspace/tts-audiobook-tool/      # git checkout
/workspace/audiobook-data/
  python/                          # managed CPython 3.12
  tools/uv-bootstrap/              # pinned uv installer
  venvs/qwen/                      # Torch 2.8 / Qwen3
  venvs/moss/                      # Torch 2.9.1 / MOSS
  cache/huggingface/               # downloaded model weights
  cache/pip/                       # Python wheel cache
  references/                      # private voice-reference recordings
  projects/                        # private EPUBs and audio projects
  logs/                            # setup logs
```

Use `AUDIOBOOK_PROJECTS_DIR` and `AUDIOBOOK_REFERENCES_DIR` as the locations
for imported book files and reference voice samples, respectively. Do not
place either in the publicly accessible git directory. The application asks
for a working project directory when creating/opening an audiobook.

## 3. Launch the upstream application

```bash
cd /workspace/tts-audiobook-tool

bash runpod/start.sh                # existing model-selection menu
bash runpod/start.sh --model qwen   # Qwen3-TTS environment directly
bash runpod/start.sh --model moss   # MOSS-TTS environment directly
```

These commands require an **interactive** terminal. Use the existing app's
project creation/import, language selection (`de`), reference voice selection,
generation and export menus. For Qwen, select
`Qwen/Qwen3-TTS-12Hz-1.7B-Base` for voice cloning; MOSS is selected from
its model settings. Actual weights are fetched on first model load and then
stored in `HF_HOME` on the persistent volume.

```bash
bash runpod/start.sh --doctor
# or report without failing when no GPU is connected:
python3 runpod/doctor.py --json
```

The doctor checks the volume mount, GPU visibility, FFmpeg, PortAudio, Python
venvs, module visibility and CUDA availability. It **does not** load a full
model or synthesize audio, so a green diagnostic is not an inference benchmark.

## 4. Real-GPU acceptance checklist (must be completed on RunPod)

- [ ] Record base image/driver, GPU name and VRAM from `nvidia-smi`.
- [ ] Install Qwen and MOSS; save setup logs and the requirements fingerprints.
- [ ] Launch each venv, load the intended actual model, and render the **same
      original, non-copyrighted German test sentence**:
      `Gute Vertriebsführung verbindet klare Entscheidungen mit aufmerksamem Zuhören.`
- [ ] Listen for clarity and accent. Verify both generated sound files are
      nonempty and play fully, without omissions or repeated words.
- [ ] Record each model's loaded VRAM, output length, generation wall time and
      real-time factor (generation seconds / audio seconds).
- [ ] Stop and restart the Pod, confirm caches/venvs/projects persist and
      repeat the short generation without reinstalling dependencies.
- [ ] Document model/checkpoint licenses and any access requirements; **do not
      commit tokens, voice reference audio or book content**.

Model choice, German voice authenticity and final audiobook quality remain
out of scope for P0; see Issues #3, #4 and #5.

## Troubleshooting

- **Mount check fails:** attach a volume at exactly `/workspace` when creating
  the Pod. Do not bypass the check on a real Pod; doing so risks data loss.
- **Out-of-memory:** capture model, GPU, and memory in the report; use a larger
  VRAM Pod or test one model environment at a time. The venvs do not load both
  models simultaneously.
- **CUDA not available in installed venv:** confirm compatible host NVIDIA
  driver/CUDA and the PyTorch wheel installed. A visible GPU in `nvidia-smi`
  alone does not prove PyTorch can use it.
- **A pip/model install fails:** inspect the persisted setup log, resolve
  the dependency or network error, and rerun the same setup command. The
  setup marker is only written on successful package checks.
- **Upstream requirements change:** the fingerprint invalidates and pip
  rechecks the affected venv. Model downloads remain in the shared HF cache.
- **Public services:** do not forward the TUI as an unauthenticated web
  endpoint. The existing player is optional and not exposed by these scripts.

Relevant upstream files: `AGENTS.md`, `launch.py`,
`requirements-qwen3tts.txt`, `requirements-moss.txt`, and
`docs-dev/test-routing.md`.
