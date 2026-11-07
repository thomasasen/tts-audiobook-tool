#!/usr/bin/env bash
# Source this file in RunPod helper scripts. Data stays outside the git checkout.
: "${RUNPOD_WORKSPACE:=/workspace}"
case "${RUNPOD_WORKSPACE}" in
  /*) ;;
  *) printf 'RUNPOD_WORKSPACE must be an absolute path\n' >&2; return 2 2>/dev/null || exit 2 ;;
esac
: "${AUDIOBOOK_DATA_ROOT:=${RUNPOD_WORKSPACE%/}/audiobook-data}"
AUDIOBOOK_VENV_DIR="$AUDIOBOOK_DATA_ROOT/venvs"
AUDIOBOOK_PROJECTS_DIR="$AUDIOBOOK_DATA_ROOT/projects"
AUDIOBOOK_REFERENCES_DIR="$AUDIOBOOK_DATA_ROOT/references"
AUDIOBOOK_LOG_DIR="$AUDIOBOOK_DATA_ROOT/logs"
AUDIOBOOK_APP_HOME="$AUDIOBOOK_DATA_ROOT/home"
HF_HOME="$AUDIOBOOK_DATA_ROOT/cache/huggingface"
HF_HUB_CACHE="$HF_HOME/hub"
TORCH_HOME="$AUDIOBOOK_DATA_ROOT/cache/torch"
XDG_CACHE_HOME="$AUDIOBOOK_DATA_ROOT/cache/xdg"
PIP_CACHE_DIR="$AUDIOBOOK_DATA_ROOT/cache/pip"
UV_CACHE_DIR="$AUDIOBOOK_DATA_ROOT/cache/uv"
UV_PYTHON_INSTALL_DIR="$AUDIOBOOK_DATA_ROOT/python"
UV_PYTHON_PREFERENCE="only-managed"
# RunPod images may set HF_HUB_ENABLE_HF_TRANSFER=1 while the isolated
# Thorsten venv does not include the optional hf_transfer package.
# Use the supported standard HTTP downloader in all RunPod child processes.
HF_HUB_ENABLE_HF_TRANSFER=0
export HF_HUB_ENABLE_HF_TRANSFER
export RUNPOD_WORKSPACE AUDIOBOOK_DATA_ROOT AUDIOBOOK_VENV_DIR AUDIOBOOK_PROJECTS_DIR
export AUDIOBOOK_REFERENCES_DIR AUDIOBOOK_LOG_DIR AUDIOBOOK_APP_HOME
export HF_HOME HF_HUB_CACHE TORCH_HOME XDG_CACHE_HOME PIP_CACHE_DIR
export UV_CACHE_DIR UV_PYTHON_INSTALL_DIR UV_PYTHON_PREFERENCE
