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


def export_remaining_seconds(
    checkpoints: list[tuple[int, float]], processed: int, total: int,
) -> int | None:
    """Estimate *encoding* time from real completed export checkpoints.

    The caller must suppress this during the final container flush/mux stage:
    that duration cannot be inferred from segment throughput. At least three
    positive checkpoint intervals are required. No progress implies no ETA.
    """
    if (not isinstance(processed, int) or not isinstance(total, int)
            or not 0 < processed < total):
        return None
    rates: list[float] = []
    previous = None
    for entry in checkpoints[-9:]:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            return None
        count, elapsed = entry
        if (type(count) is not int or count <= 0 or count > processed
                or not isinstance(elapsed, (int, float))
                or not math.isfinite(elapsed) or elapsed < 0):
            return None
        if previous is not None:
            delta_count = count - previous[0]
            delta_seconds = elapsed - previous[1]
            if delta_count <= 0 or delta_seconds <= 0:
                return None
            rates.append(delta_seconds / delta_count)
        previous = (count, elapsed)
    if len(rates) < 3 or previous is None or previous[0] != processed:
        return None
    return max(1, math.ceil(median(rates[-7:]) * (total - processed)))
