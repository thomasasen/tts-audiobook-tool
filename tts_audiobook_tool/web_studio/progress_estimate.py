"""Conservative ETA estimation for per-segment TTS jobs.

Estimates are based on ACTUAL per-segment wall-clock processing times, not
fabricated progress. A rolling median prevents long outliers dominating.
The export stage has separate progress and is not included in synthesis ETA.
"""
from __future__ import annotations

import math
from statistics import median


def remaining_seconds(samples: list[tuple[int, float]], remaining_chars: int) -> int | None:
    """Seconds remaining for synthesis, or None until at least 3 real samples.

    The estimate tracks processing seconds per character, including waveform
    validation and ASR QC. Sentence difficulty and voice inference time vary.
    """
    if remaining_chars <= 0:
        return 0
    per_char: list[float] = []
    for chars, seconds in samples[-12:]:
        if (not isinstance(chars, int) or chars <= 0 or
            not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or
            seconds <= 0 or seconds > 3600):
            continue
        per_char.append(seconds / chars)
    if len(per_char) < 3:
        return None
    rate = median(per_char[-8:])
    return max(1, math.ceil(remaining_chars * rate))
