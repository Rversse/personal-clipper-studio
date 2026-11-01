from __future__ import annotations

from bisect import bisect_left, bisect_right
import math
import re
from typing import Any


_CONTINUATION_STARTS = (
    "dan ", "atau ", "karena ", "sehingga ", "yang ", "dengan ",
    "tetapi ", "tapi ", "namun ", "jadi ", "maka ", "untuk ",
    "kalau ", "jika ", "ketika ", "sementara ", "bahwa ", "seperti ",
    "misalnya ", "yakni ", "yaitu ", "di mana ", "yang mana ",
)
_INCOMPLETE_ENDINGS = {
    "dan", "atau", "karena", "sehingga", "yang", "dengan", "tetapi",
    "tapi", "namun", "untuk", "kalau", "jika", "ketika", "sementara",
    "bahwa", "di", "ke", "dari", "pada", "adalah", "merupakan",
}
_SENTENCE_END = re.compile(r"[.!?…][\"'”’»）】]*$")
_CLOSING_PUNCTUATION = re.compile(r"[\"'”’»）】]+$")


def _text(segment: dict[str, Any]) -> str:
    value = segment.get("text", "")
    return value.strip() if isinstance(value, str) else ""


def _ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END.search(text.strip()))


def _starts_continuation(text: str) -> bool:
    lowered = text.strip().lower()
    return any(lowered.startswith(prefix) for prefix in _CONTINUATION_STARTS)


def _trailing_fragment(text: str) -> bool:
    cleaned = _CLOSING_PUNCTUATION.sub("", text.strip()).lower()
    words = re.findall(r"[\w'-]+", cleaned)
    return bool(words and words[-1] in _INCOMPLETE_ENDINGS)


def _gap(left: dict[str, Any], right: dict[str, Any]) -> float:
    return max(0.0, float(right["start"]) - float(left["end"]))




def _prepare_audio_features(
    audio_features: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build reusable time indexes for audio boundary scoring.

    RMS samples are emitted in timestamp order. Preparing the indexes once per
    candidate refinement avoids rescanning the entire recording for each word.
    """
    if not isinstance(audio_features, dict):
        return None
    if audio_features.get("_boundary_indexed") is True:
        return audio_features

    silence_starts: list[float] = []
    silence_ends: list[float] = []
    for silence in audio_features.get("silences", []):
        if not isinstance(silence, dict):
            continue
        try:
            start = float(silence["start"])
            end = float(silence["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(start) and math.isfinite(end) and start >= 0 and end > start:
            silence_starts.append(start)
            silence_ends.append(end)

    samples: list[tuple[float, float]] = []
    for sample in audio_features.get("energy_samples", []):
        if not isinstance(sample, dict):
            continue
        try:
            sample_time = float(sample["time"])
            rms_db = float(sample["rms_db"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(sample_time) and sample_time >= 0 and math.isfinite(rms_db):
            samples.append((sample_time, rms_db))
    samples.sort(key=lambda sample: sample[0])

    energy_times = tuple(sample[0] for sample in samples)
    energy_prefix = [0.0]
    for _, level in samples:
        energy_prefix.append(energy_prefix[-1] + level)

    return {
        "_boundary_indexed": True,
        "_silence_starts": tuple(sorted(silence_starts)),
        "_silence_ends": tuple(sorted(silence_ends)),
        "_energy_times": energy_times,
        "_energy_prefix": tuple(energy_prefix),
    }


def _audio_boundary_adjustment(
    timestamp: float,
    audio_features: dict[str, Any] | None,
    edge: str,
) -> tuple[int, list[str]]:
    """Return a small prosody adjustment from nearby pauses and RMS changes."""
    if not isinstance(audio_features, dict) or edge not in {"opening", "ending"}:
        return 0, []
    indexed = (
        audio_features
        if audio_features.get("_boundary_indexed") is True
        else _prepare_audio_features(audio_features)
    )
    if indexed is None:
        return 0, []

    adjustment = 0
    reasons: list[str] = []

    if edge == "opening":
        silence_values = indexed["_silence_ends"]
        pause_reason = "setelah jeda audio alami"
    else:
        silence_values = indexed["_silence_starts"]
        pause_reason = "sebelum jeda audio alami"
    silence_index = bisect_left(silence_values, timestamp - 0.4)
    if (
        silence_index < len(silence_values)
        and silence_values[silence_index] <= timestamp + 0.4
    ):
        adjustment += 10
        reasons.append(pause_reason)

    times = indexed["_energy_times"]
    prefix = indexed["_energy_prefix"]
    before_left = bisect_left(times, timestamp - 1.5)
    before_right = bisect_right(times, timestamp - 0.15)
    after_left = bisect_left(times, timestamp + 0.15)
    after_right = bisect_right(times, timestamp + 1.5)
    before_count = before_right - before_left
    after_count = after_right - after_left
    if before_count > 0 and after_count > 0:
        before_level = (prefix[before_right] - prefix[before_left]) / before_count
        after_level = (prefix[after_right] - prefix[after_left]) / after_count
        change = after_level - before_level
        if edge == "opening":
            if change >= 4.0:
                adjustment += 4
                reasons.append("energi bicara menguat pada pembuka")
            elif change <= -6.0:
                adjustment -= 3
                reasons.append("energi bicara melemah pada pembuka")
        else:
            if change <= -4.0:
                adjustment += 4
                reasons.append("energi bicara mereda pada penutup")
            elif change >= 6.0:
                adjustment -= 3
                reasons.append("energi bicara masih menguat setelah penutup")

    return max(-8, min(14, adjustment)), reasons
def _opening_signal(
    segments: list[dict[str, Any]],
    index: int,
    audio_features: dict[str, Any] | None = None,
) -> tuple[int, str]:
    segment = segments[index]
    current = _text(segment)
    previous = segments[index - 1] if index > 0 else None
    score = 52
    reasons: list[str] = []

    if previous is None:
        score += 22
        reasons.append("awal transkrip")
    else:
        previous_text = _text(previous)
        gap = _gap(previous, segment)
        if _ends_sentence(previous_text):
            score += 20
            reasons.append("awal kalimat baru")
        if gap >= 0.45:
            score += min(16, int(gap * 12))
            reasons.append("jeda alami")
        if _trailing_fragment(previous_text):
            score -= 18
            reasons.append("kalimat sebelumnya belum tuntas")

    if current[:1].isupper():
        score += 5
    elif current[:1].islower():
        score -= 20
        reasons.append("melanjutkan kalimat sebelumnya")
    if _starts_continuation(current):
        score -= 22
        reasons.append("dimulai dengan kata sambung")
    audio_adjustment, audio_reasons = _audio_boundary_adjustment(
        float(segment["start"]), audio_features, "opening"
    )
    score += audio_adjustment
    reasons.extend(audio_reasons)
    if not reasons:
        reasons.append("batas segmen transkrip")
    return max(0, min(100, score)), ", ".join(reasons)


def _ending_signal(
    segments: list[dict[str, Any]],
    index: int,
    audio_features: dict[str, Any] | None = None,
) -> tuple[int, str]:
    segment = segments[index]
    current = _text(segment)
    following = segments[index + 1] if index + 1 < len(segments) else None
    score = 52
    reasons: list[str] = []

    if _ends_sentence(current):
        score += 27
        reasons.append("akhir kalimat")
    if _trailing_fragment(current):
        score -= 24
        reasons.append("ujung kalimat menggantung")

    if following is None:
        score += 12
        reasons.append("akhir transkrip")
    else:
        gap = _gap(segment, following)
        if gap >= 0.45:
            score += min(16, int(gap * 12))
            reasons.append("jeda alami")
        next_text = _text(following)
        if _starts_continuation(next_text) and not _ends_sentence(current):
            score -= 10
            reasons.append("gagasan berlanjut ke segmen berikutnya")

    audio_adjustment, audio_reasons = _audio_boundary_adjustment(
        float(segment["end"]), audio_features, "ending"
    )
    score += audio_adjustment
    reasons.extend(audio_reasons)
    if not reasons:
        reasons.append("batas segmen transkrip")
    return max(0, min(100, score)), ", ".join(reasons)



_CONTINUATION_WORDS = {
    "dan", "atau", "karena", "sehingga", "yang", "dengan", "tetapi",
    "tapi", "namun", "jadi", "maka", "untuk", "kalau", "jika", "ketika",
    "sementara", "bahwa", "seperti", "misalnya", "yakni", "yaitu", "di",
    "ke", "dari", "pada", "adalah", "merupakan",
}


def _word_starts_continuation(text: str) -> bool:
    cleaned = _CLOSING_PUNCTUATION.sub("", text.strip().lower())
    match = re.match(r"[^\W_'-]+", cleaned)
    return bool(match and match.group(0) in _CONTINUATION_WORDS)


def _collect_valid_words(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten ordered word timestamps, dropping malformed or overlapping words."""
    words: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(segments):
        try:
            segment_start = float(segment["start"])
            segment_end = float(segment["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        raw_words = segment.get("words")
        if not isinstance(raw_words, list):
            continue
        for word_index, word in enumerate(raw_words):
            if not isinstance(word, dict):
                continue
            try:
                start = float(word["start"])
                end = float(word["end"])
                text = str(word.get("text", "")).strip()
                raw_probability = word.get("probability")
                probability = float(raw_probability) if raw_probability is not None else None
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if (
                not text
                or not math.isfinite(start)
                or not math.isfinite(end)
                or start < segment_start - 0.02
                or end > segment_end + 0.02
                or end <= start
                or (
                    probability is not None
                    and (not math.isfinite(probability) or not 0 <= probability <= 1)
                )
            ):
                continue
            if words and start < words[-1]["end"] - 0.02:
                continue
            words.append({
                "start": start,
                "end": end,
                "text": text,
                "probability": probability,
                "segment_index": segment_index,
                "word_index": word_index,
            })
    return words


def _word_opening_signal(
    words: list[dict[str, Any]],
    index: int,
    segments: list[dict[str, Any]],
    audio_features: dict[str, Any] | None = None,
) -> tuple[int, str]:
    current = words[index]
    previous = words[index - 1] if index > 0 else None
    score = 52
    reasons = ["batas kata"]

    if previous is None:
        score += 16
        reasons.append("awal transkrip")
    else:
        gap = max(0.0, current["start"] - previous["end"])
        if _ends_sentence(previous["text"]):
            score += 22
            reasons.append("awal kalimat baru")
        if gap >= 0.35:
            score += min(18, int(gap * 12))
            reasons.append("jeda alami antar kata")
        if _trailing_fragment(previous["text"]):
            score -= 18
            reasons.append("kata sebelumnya belum menuntaskan gagasan")

    if _word_starts_continuation(current["text"]):
        score -= 24
        reasons.append("dimulai dengan kata sambung")
    if current["probability"] is not None and current["probability"] < 0.45:
        score -= 5
        reasons.append("confidence timestamp kata rendah")

    is_first_word_in_segment = (
        previous is None or previous["segment_index"] != current["segment_index"]
    )
    if is_first_word_in_segment:
        segment_quality, _ = _opening_signal(
            segments, current["segment_index"], audio_features
        )
        if segment_quality >= 75:
            score += min(7, round((segment_quality - 65) * 0.25))
            reasons.append("konteks awal segmen mendukung")

    audio_adjustment, audio_reasons = _audio_boundary_adjustment(
        current["start"], audio_features, "opening"
    )
    score += audio_adjustment
    reasons.extend(audio_reasons)
    return max(0, min(100, score)), ", ".join(reasons)


def _word_ending_signal(
    words: list[dict[str, Any]],
    index: int,
    segments: list[dict[str, Any]],
    audio_features: dict[str, Any] | None = None,
) -> tuple[int, str]:
    current = words[index]
    following = words[index + 1] if index + 1 < len(words) else None
    score = 52
    reasons = ["batas kata"]

    if _ends_sentence(current["text"]):
        score += 27
        reasons.append("akhir kalimat")
    if _trailing_fragment(current["text"]):
        score -= 24
        reasons.append("ujung kalimat menggantung")

    if following is None:
        score += 12
        reasons.append("akhir transkrip")
    else:
        gap = max(0.0, following["start"] - current["end"])
        if gap >= 0.35:
            score += min(18, int(gap * 12))
            reasons.append("jeda alami antar kata")
        if _word_starts_continuation(following["text"]) and not _ends_sentence(current["text"]):
            score -= 10
            reasons.append("gagasan berlanjut ke kata berikutnya")

    if current["probability"] is not None and current["probability"] < 0.45:
        score -= 5
        reasons.append("confidence timestamp kata rendah")

    is_last_word_in_segment = (
        following is None or following["segment_index"] != current["segment_index"]
    )
    if is_last_word_in_segment:
        segment_quality, _ = _ending_signal(
            segments, current["segment_index"], audio_features
        )
        if segment_quality >= 75:
            score += min(7, round((segment_quality - 65) * 0.25))
            reasons.append("konteks akhir segmen mendukung")

    audio_adjustment, audio_reasons = _audio_boundary_adjustment(
        current["end"], audio_features, "ending"
    )
    score += audio_adjustment
    reasons.extend(audio_reasons)
    return max(0, min(100, score)), ", ".join(reasons)


def _refine_with_word_boundaries(
    target_start: float,
    target_end: float,
    segments: list[dict[str, Any]],
    min_duration: int,
    max_duration: int,
    audio_features: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    words = _collect_valid_words(segments)
    if not words:
        return None

    starts = []
    for index, word in enumerate(words):
        boundary = word["start"]
        if target_start - 10.0 <= boundary <= target_start + 2.0:
            quality, reason = _word_opening_signal(
                words, index, segments, audio_features
            )
            starts.append((boundary, quality, reason, index))

    ends = []
    for index, word in enumerate(words):
        boundary = word["end"]
        if target_end - 2.0 <= boundary <= target_end + 10.0:
            quality, reason = _word_ending_signal(
                words, index, segments, audio_features
            )
            ends.append((boundary, quality, reason, index))

    if not starts or not ends:
        return None

    best: tuple[float, dict[str, Any]] | None = None
    for start, start_quality, start_reason, start_index in starts:
        for end, end_quality, end_reason, end_index in ends:
            duration = end - start
            if end_index < start_index or not min_duration <= duration <= max_duration:
                continue
            start_delta = abs(start - target_start)
            end_delta = abs(end - target_end)
            score = (
                start_quality * 0.44
                + end_quality * 0.56
                - start_delta * 2.5
                - end_delta * 2.0
            )
            result = {
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "boundary_quality": round((start_quality + end_quality) / 2),
                "opening_boundary": f"Presisi kata: {start_reason}",
                "ending_boundary": f"Presisi kata: {end_reason}",
                "context_note": (
                    f"Pembuka: {start_reason}. Penutup: {end_reason}. "
                    "Timestamp dipaskan ke batas kata Whisper."
                ),
            }
            if best is None or score > best[0]:
                best = (score, result)

    return best[1] if best else None


def refine_candidate_boundaries(
    target_start: float,
    target_end: float,
    segments: list[dict[str, Any]],
    min_duration: int,
    max_duration: int,
    audio_features: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Snap an AI proposal to word boundaries and use local audio prosody when available.

    The proposal remains the semantic anchor. Starts may move up to 10 seconds
    earlier (or 2 seconds later), while endings may move up to 10 seconds later
    (or 2 seconds earlier), subject to the requested duration window.
    """
    if not segments or not all(math.isfinite(float(value)) for value in (target_start, target_end)):
        return None
    if target_end <= target_start or min_duration <= 0 or max_duration < min_duration:
        return None
    prepared_audio = _prepare_audio_features(audio_features)
    proposed_duration = target_end - target_start
    # Boundary refinement may recover a small amount of missing setup/payoff,
    # but must not stretch an invalid proposal into the requested duration band.
    if proposed_duration < min_duration - 5 or proposed_duration > max_duration + 5:
        return None

    ordered = sorted(segments, key=lambda item: float(item["start"]))
    if any(
        not math.isfinite(float(item["start"]))
        or not math.isfinite(float(item["end"]))
        or float(item["start"]) < 0
        or float(item["end"]) <= float(item["start"])
        for item in ordered
    ):
        return None

    word_refined = _refine_with_word_boundaries(
        target_start, target_end, ordered, min_duration, max_duration, prepared_audio
    )
    if word_refined is not None:
        return word_refined

    starts = []
    for index, segment in enumerate(ordered):
        boundary = float(segment["start"])
        if target_start - 10.0 <= boundary <= target_start + 2.0:
            quality, reason = _opening_signal(ordered, index, prepared_audio)
            starts.append((boundary, quality, reason, index))

    ends = []
    for index, segment in enumerate(ordered):
        boundary = float(segment["end"])
        if target_end - 2.0 <= boundary <= target_end + 10.0:
            quality, reason = _ending_signal(ordered, index, prepared_audio)
            ends.append((boundary, quality, reason, index))

    best: tuple[float, dict[str, Any]] | None = None
    for start, start_quality, start_reason, start_index in starts:
        for end, end_quality, end_reason, end_index in ends:
            duration = end - start
            if not min_duration <= duration <= max_duration:
                continue
            if end_index < start_index:
                continue
            start_delta = abs(start - target_start)
            end_delta = abs(end - target_end)
            score = (
                start_quality * 0.44
                + end_quality * 0.56
                - start_delta * 2.5
                - end_delta * 2.0
            )
            result = {
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "boundary_quality": round((start_quality + end_quality) / 2),
                "opening_boundary": start_reason,
                "ending_boundary": end_reason,
                "context_note": (
                    f"Pembuka: {start_reason}. Penutup: {end_reason}."
                ),
            }
            if best is None or score > best[0]:
                best = (score, result)

    return best[1] if best else None
