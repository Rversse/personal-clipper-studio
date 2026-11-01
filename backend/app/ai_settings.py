from __future__ import annotations

import http.client
import json
import os
import ssl
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request

from . import credentials

router = APIRouter()


def _model_name() -> str:
    return os.environ.get("PCS_GEMINI_MODEL", "gemini-3.8-flash").strip()


def _settings_payload() -> dict[str, Any]:
    status = credentials.gemini_key_status()
    return {
        "provider": os.environ.get("PCS_AI_PROVIDER", "gemini").strip().lower(),
        "model": _model_name() or None,
        **status,
    }


def _test_gemini_key(api_key: str, model: str) -> dict[str, Any]:
    """Validate key and model access with a read-only Gemini model metadata request."""
    if not model or len(model) > 128 or any(ord(char) < 32 for char in model):
        return {"success": False, "status": "invalid_model", "message": "Nama model Gemini tidak valid."}

    connection = http.client.HTTPSConnection(
        "generativelanguage.googleapis.com",
        timeout=12,
        context=ssl.create_default_context(),
    )
    path = f"/v1beta/models/{quote(model.removeprefix('models/'), safe='')}"
    try:
        connection.request(
            "GET",
            path,
            headers={
                "Accept": "application/json",
                "x-goog-api-key": api_key,
            },
        )
        response = connection.getresponse()
        raw = response.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            return {"success": False, "status": "invalid_response", "message": "Respons Gemini terlalu besar."}
        if response.status == 200:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {"success": False, "status": "invalid_response", "message": "Respons Gemini tidak valid."}
            if isinstance(payload, dict) and isinstance(payload.get("name"), str):
                return {"success": True, "status": "connected", "message": "API key valid dan model dapat diakses."}
            return {"success": False, "status": "invalid_response", "message": "Format respons Gemini tidak dikenali."}
        if response.status in (400, 401, 403):
            return {"success": False, "status": "authentication_failed", "message": "API key ditolak atau tidak memiliki izin untuk model ini."}
        if response.status == 404:
            return {"success": False, "status": "model_not_found", "message": "Model tidak ditemukan atau tidak tersedia untuk API key ini."}
        if response.status == 429:
            return {"success": False, "status": "rate_limited", "message": "Gemini membatasi permintaan. Coba lagi beberapa saat."}
        return {"success": False, "status": "provider_error", "message": f"Gemini mengembalikan HTTP {response.status}."}
    except (OSError, TimeoutError, http.client.HTTPException, ssl.SSLError):
        return {"success": False, "status": "unreachable", "message": "Tidak dapat menghubungi Gemini. Periksa koneksi internet."}
    finally:
        connection.close()


@router.get("/api/settings/ai")
def get_ai_settings() -> dict[str, Any]:
    return _settings_payload()


@router.put("/api/settings/ai/gemini-key")
async def put_gemini_key(request: Request) -> dict[str, Any]:
    body_chunks: list[bytes] = []
    body_size = 0
    async for chunk in request.stream():
        body_size += len(chunk)
        if body_size > 4096:
            raise HTTPException(status_code=413, detail="Request konfigurasi terlalu besar.")
        body_chunks.append(chunk)
    try:
        payload = json.loads(b"".join(body_chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=400, detail="Format request konfigurasi tidak valid.") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("api_key"), str):
        raise HTTPException(status_code=422, detail="Gemini API key harus berupa teks.")
    try:
        credentials.save_gemini_api_key(payload["api_key"])
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except credentials.CredentialStoreUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except credentials.CredentialStoreError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _settings_payload()


@router.delete("/api/settings/ai/gemini-key")
def remove_gemini_key() -> dict[str, Any]:
    try:
        credentials.delete_gemini_api_key()
    except credentials.CredentialStoreUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except credentials.CredentialStoreError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _settings_payload()


@router.post("/api/settings/ai/gemini-test")
def test_gemini_key() -> dict[str, Any]:
    try:
        key = credentials.get_gemini_api_key()
    except credentials.CredentialStoreError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if not key:
        raise HTTPException(status_code=409, detail="Gemini API key belum dikonfigurasi.")
    result = _test_gemini_key(key, _model_name())
    return result
