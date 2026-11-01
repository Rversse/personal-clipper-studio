from __future__ import annotations

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


def _opening_signal(segments: list[dict[str, Any]], index: int) -> tuple[int, str]:
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
    if not reasons:
        reasons.append("batas segmen transkrip")
    return max(0, min(100, score)), ", ".join(reasons)


def _ending_signal(segments: list[dict[str, Any]], index: int) -> tuple[int, str]:
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

    if not reasons:
        reasons.append("batas segmen transkrip")
    return max(0, min(100, score)), ", ".join(reasons)


def refine_candidate_boundaries(
    target_start: float,
    target_end: float,
    segments: list[dict[str, Any]],
    min_duration: int,
    max_duration: int,
) -> dict[str, Any] | None:
    """Snap an AI proposal to sentence- and pause-aware Whisper segment boundaries.

    The proposal remains the semantic anchor. Starts may move up to 10 seconds
    earlier (or 2 seconds later), while endings may move up to 10 seconds later
    (or 2 seconds earlier), subject to the requested duration window.
    """
    if not segments or not all(math.isfinite(float(value)) for value in (target_start, target_end)):
        return None
    if target_end <= target_start or min_duration <= 0 or max_duration < min_duration:
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

    starts = []
    for index, segment in enumerate(ordered):
        boundary = float(segment["start"])
        if target_start - 10.0 <= boundary <= target_start + 2.0:
            quality, reason = _opening_signal(ordered, index)
            starts.append((boundary, quality, reason, index))

    ends = []
    for index, segment in enumerate(ordered):
        boundary = float(segment["end"])
        if target_end - 2.0 <= boundary <= target_end + 10.0:
            quality, reason = _ending_signal(ordered, index)
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
