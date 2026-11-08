"""Verified controls for the pinned Thorsten-CosyVoice3 SFT runtime.

The official infer_thorsten.py explicitly exposes inference_sft(..., speed=...).
No unsupported voice-style instructions, emotion controls or text prompt
injection into the fixed SFT frontend are accepted here.
"""
from __future__ import annotations

import math

SPEED_OPTIONS = (0.85, 0.90, 0.95, 1.0, 1.05, 1.10, 1.15)
DEFAULT_SETTINGS = {"speed": 1.0, "preview_text": ""}
MAX_PREVIEW_TEXT_LENGTH = 600


def verified_speed(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Ungültiges Sprechtempo.")
    for allowed in SPEED_OPTIONS:
        if abs(value - allowed) < 1e-6:
            return allowed
    raise ValueError("Sprechtempo muss zwischen 0,85 und 1,15 in Schritten von 0,05 liegen.")


def verified_preview_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Der Hörprobentext muss Text sein.")
    value = value.strip()
    if (len(value) > MAX_PREVIEW_TEXT_LENGTH or
            (value and len(value) < 5) or
            any(ord(char) < 32 and char != "\n" for char in value)):
        raise ValueError("Hörprobentext: 5 bis 600 Zeichen, keine Steuerzeichen.")
    return value


def verified_settings(data: object) -> dict:
    if not isinstance(data, dict) or set(data) != {"speed", "preview_text"}:
        raise ValueError("Ungültige Einstellungen der deutschen Stimme.")
    return {
        "speed": verified_speed(data["speed"]),
        "preview_text": verified_preview_text(data["preview_text"]),
    }
