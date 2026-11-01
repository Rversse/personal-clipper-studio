from __future__ import annotations

import os
import re
from typing import Any

SERVICE_NAME = "PersonalClipperStudio"
ACCOUNT_NAME = "gemini-api-key"
MAX_API_KEY_LENGTH = 512


class CredentialStoreUnavailable(RuntimeError):
    """Raised when the Windows Credential Manager cannot be used safely."""


class CredentialStoreError(RuntimeError):
    """Raised when Windows Credential Manager fails an operation."""


def _windows_backend() -> Any:
    if os.name != "nt":
        raise CredentialStoreUnavailable(
            "Penyimpanan kredensial aman hanya tersedia pada Windows."
        )
    try:
        from keyring.backends.Windows import WinVaultKeyring

        return WinVaultKeyring()
    except Exception as error:
        raise CredentialStoreUnavailable(
            "Windows Credential Manager tidak tersedia pada perangkat ini."
        ) from error


def validate_api_key(value: str) -> str:
    key = value.strip()
    if not key:
        raise ValueError("Gemini API key tidak boleh kosong.")
    if len(key) > MAX_API_KEY_LENGTH or re.search(r"[\x00-\x1f\x7f]", key):
        raise ValueError("Format Gemini API key tidak valid.")
    return key


def get_gemini_api_key() -> str | None:
    """Environment configuration takes precedence over the per-user vault secret."""
    environment_key = os.environ.get("PCS_GEMINI_API_KEY", "").strip()
    if environment_key:
        return validate_api_key(environment_key)
    try:
        stored_key = _windows_backend().get_password(SERVICE_NAME, ACCOUNT_NAME)
        return validate_api_key(stored_key) if stored_key else None
    except CredentialStoreUnavailable:
        return None
    except ValueError as error:
        raise CredentialStoreError("Gemini API key tersimpan memiliki format yang tidak valid.") from error
    except Exception as error:
        raise CredentialStoreError(
            "Tidak dapat membaca Gemini API key dari Windows Credential Manager."
        ) from error


def gemini_key_status() -> dict[str, Any]:
    environment_key = os.environ.get("PCS_GEMINI_API_KEY", "").strip()
    if environment_key:
        try:
            _windows_backend()
            vault_available = True
        except CredentialStoreUnavailable:
            vault_available = False
        return {
            "configured": True,
            "source": "environment",
            "credential_store_available": vault_available,
            "managed_by_environment": True,
        }
    try:
        backend = _windows_backend()
        key = backend.get_password(SERVICE_NAME, ACCOUNT_NAME)
        return {
            "configured": bool(key),
            "source": "windows_credential_manager" if key else "missing",
            "credential_store_available": True,
            "managed_by_environment": False,
        }
    except CredentialStoreUnavailable:
        return {
            "configured": False,
            "source": "unavailable",
            "credential_store_available": False,
            "managed_by_environment": False,
        }
    except Exception:
        return {
            "configured": False,
            "source": "error",
            "credential_store_available": False,
            "managed_by_environment": False,
        }


def save_gemini_api_key(value: str) -> None:
    key = validate_api_key(value)
    if os.environ.get("PCS_GEMINI_API_KEY", "").strip():
        raise CredentialStoreError(
            "Gemini API key sedang dikelola melalui environment variable."
        )
    try:
        _windows_backend().set_password(SERVICE_NAME, ACCOUNT_NAME, key)
    except CredentialStoreUnavailable:
        raise
    except Exception as error:
        raise CredentialStoreError(
            "Gagal menyimpan Gemini API key ke Windows Credential Manager."
        ) from error


def delete_gemini_api_key() -> bool:
    if os.environ.get("PCS_GEMINI_API_KEY", "").strip():
        raise CredentialStoreError(
            "Gemini API key sedang dikelola melalui environment variable."
        )
    try:
        backend = _windows_backend()
        existing = backend.get_password(SERVICE_NAME, ACCOUNT_NAME)
        if not existing:
            return False
        backend.delete_password(SERVICE_NAME, ACCOUNT_NAME)
        return True
    except CredentialStoreUnavailable:
        raise
    except Exception as error:
        raise CredentialStoreError(
            "Gagal menghapus Gemini API key dari Windows Credential Manager."
        ) from error
