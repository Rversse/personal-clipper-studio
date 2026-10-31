from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


DISCOVERY_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_seconds": {"type": "number"},
                    "end_seconds": {"type": "number"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "summary": {"type": "string"},
                    "rationale": {"type": "string"},
                    "score": {"type": "integer"},
                    "category": {"type": "string"},
                },
                "required": [
                    "start_seconds", "end_seconds", "title", "hook",
                    "summary", "rationale", "score", "category",
                ],
            },
        }
    },
    "required": ["candidates"],
}


class DiscoveryProviderError(RuntimeError):
    pass


def provider_settings() -> dict[str, str]:
    provider = os.environ.get("PCS_AI_PROVIDER", "gemini").strip().lower()
    if provider not in {"gemini", "ollama"}:
        raise DiscoveryProviderError("Provider AI tidak dikenal. Pilih gemini atau ollama.")
    if provider == "gemini":
        api_key = os.environ.get("PCS_GEMINI_API_KEY", "").strip()
        model = os.environ.get("PCS_GEMINI_MODEL", "gemini-3.8-flash").strip()
        if not model:
            raise DiscoveryProviderError("PCS_GEMINI_MODEL tidak boleh kosong.")
        if not api_key:
            raise DiscoveryProviderError(
                "Gemini API key belum dikonfigurasi. Set PCS_GEMINI_API_KEY di environment lokal."
            )
        return {
            "provider": provider,
            "api_key": api_key,
            "model": model,
        }
    model = os.environ.get("PCS_OLLAMA_MODEL", "qwen3:8b").strip()
    url = os.environ.get("PCS_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    if not model or not url:
        raise DiscoveryProviderError("PCS_OLLAMA_MODEL dan PCS_OLLAMA_URL harus diisi.")
    return {"provider": provider, "url": url, "model": model}


def _request_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 180) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        detail = error.read(2000).decode("utf-8", errors="replace")
        raise DiscoveryProviderError(
            f"Provider AI mengembalikan HTTP {error.code}: {detail[:1000]}"
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise DiscoveryProviderError(f"Tidak dapat menghubungi provider AI: {error}") from error
    if len(raw) > 8 * 1024 * 1024:
        raise DiscoveryProviderError("Respons provider AI melebihi batas 8 MB.")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DiscoveryProviderError("Respons provider AI bukan JSON yang valid.") from error


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", text.strip(), flags=re.DOTALL).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^\`\`\`(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*\`\`\`$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise DiscoveryProviderError("Output AI tidak mengikuti format JSON yang diminta.") from error
    if not isinstance(value, dict):
        raise DiscoveryProviderError("Output AI harus berupa object JSON.")
    return value


def _call_gemini(prompt: str, settings: dict[str, str]) -> dict[str, Any]:
    response = _request_json(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        {
            "model": settings["model"],
            "input": prompt,
            "store": False,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": DISCOVERY_SCHEMA,
            },
        },
        {"Content-Type": "application/json", "x-goog-api-key": settings["api_key"]},
    )
    output = response.get("output_text")
    if not isinstance(output, str) or not output.strip():
        raise DiscoveryProviderError("Gemini tidak mengembalikan output teks.")
    return _extract_json(output)


def _call_ollama(prompt: str, settings: dict[str, str]) -> dict[str, Any]:
    response = _request_json(
        f"{settings['url']}/api/chat",
        {
            "model": settings["model"],
            "messages": [
                {
                    "role": "system",
                    "content": "You are a precise video editor. Return only JSON matching the supplied schema.",
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": DISCOVERY_SCHEMA,
            "options": {"temperature": 0.2},
        },
        {"Content-Type": "application/json"},
    )
    message = response.get("message") or {}
    output = message.get("content")
    if not isinstance(output, str) or not output.strip():
        raise DiscoveryProviderError("Ollama tidak mengembalikan output teks.")
    return _extract_json(output)


def analyze_transcript_chunk(
    segments: list[dict[str, Any]],
    mode: str,
    category: str | None,
    min_duration: int,
    max_duration: int,
) -> list[dict[str, Any]]:
    if not segments:
        return []
    settings = provider_settings()
    transcript = "\n".join(
        f"[{float(item['start']):.2f} - {float(item['end']):.2f}] {item['text']}"
        for item in segments
    )
    category_instruction = (
        f"Prioritaskan karakteristik kategori: {category}."
        if mode == "category" and category
        else "Temukan momen paling kuat tanpa mengunci ke satu kategori."
    )
    prompt = f"""
You are an experienced short-form video editor. Analyze the timestamped transcript and identify up to 4 genuinely engaging clip candidates.

Task mode: {mode}
Category instruction: {category_instruction}
Target duration: {min_duration} to {max_duration} seconds.

Rules:
- Use only moments supported by the transcript. Never invent dialogue, events, or timestamps.
- Return absolute timestamps in seconds exactly on or near the supplied transcript timeline.
- Each clip must have a clear opening hook and a satisfying payoff or complete thought.
- Preserve context: include necessary setup, punchline, answer, or conclusion. Do not cut mid-sentence.
- Prefer strong opinions, surprising facts, useful explanations, emotional moments, humor, tension, and complete stories.
- Avoid dead air, repetitive points, greetings, ads, and fragments without context.
- Score editorial potential from 0 to 100. Explain the score briefly in rationale.
- Use Indonesian for title, hook, summary, and rationale when the transcript is Indonesian.
- Return an empty candidates array when no moment meets the quality bar.
- Do not stretch timestamps just to meet duration. Return only valid durations.
- category should be a short label such as podcast, gaming, education, motivation, comedy, news, storytelling, or other.

Required JSON shape:
{{"candidates":[{{"start_seconds":12.3,"end_seconds":52.1,"title":"...","hook":"...","summary":"...","rationale":"...","score":85,"category":"podcast"}}]}}

Timestamped transcript:
{transcript}
"""
    result = (
        _call_gemini(prompt, settings)
        if settings["provider"] == "gemini"
        else _call_ollama(prompt, settings)
    )
    candidates = result.get("candidates")
    if not isinstance(candidates, list):
        raise DiscoveryProviderError("Output AI tidak memiliki daftar candidates.")
    if len(candidates) > 4:
        candidates = candidates[:4]
    return candidates


def split_transcript(
    segments: list[dict[str, Any]],
    max_seconds: int = 600,
    max_chars: int = 16000,
    overlap_seconds: int = 30,
) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(segments):
        start_time = float(segments[index]["start"])
        chunk: list[dict[str, Any]] = []
        char_count = 0
        cursor = index
        while cursor < len(segments):
            item = segments[cursor]
            next_chars = len(str(item.get("text", "")))
            if chunk and (
                float(item["end"]) - start_time > max_seconds
                or char_count + next_chars > max_chars
            ):
                break
            chunk.append(item)
            char_count += next_chars
            cursor += 1
        if not chunk:
            break
        chunks.append(chunk)
        if cursor >= len(segments):
            break
        overlap_start = float(chunk[-1]["end"]) - overlap_seconds
        next_index = cursor
        while next_index > index + 1 and float(segments[next_index - 1]["start"]) > overlap_start:
            next_index -= 1
        index = max(index + 1, next_index)
    return chunks
