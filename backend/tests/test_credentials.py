from __future__ import annotations

import pytest

from app import credentials


class FakeCredentialBackend:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        del self.values[(service, account)]


def test_windows_vault_save_read_and_delete(monkeypatch):
    monkeypatch.delenv("PCS_GEMINI_API_KEY", raising=False)
    backend = FakeCredentialBackend()
    monkeypatch.setattr(credentials, "_windows_backend", lambda: backend)

    credentials.save_gemini_api_key("  test-secret-key  ")

    assert credentials.get_gemini_api_key() == "test-secret-key"
    assert credentials.gemini_key_status() == {
        "configured": True,
        "source": "windows_credential_manager",
        "credential_store_available": True,
        "managed_by_environment": False,
    }
    assert credentials.delete_gemini_api_key() is True
    assert credentials.get_gemini_api_key() is None
    assert credentials.delete_gemini_api_key() is False


def test_environment_key_has_priority_and_is_not_overwritten(monkeypatch):
    monkeypatch.setenv("PCS_GEMINI_API_KEY", "environment-secret")
    backend = FakeCredentialBackend()
    monkeypatch.setattr(credentials, "_windows_backend", lambda: backend)

    assert credentials.get_gemini_api_key() == "environment-secret"
    assert credentials.gemini_key_status()["source"] == "environment"
    with pytest.raises(credentials.CredentialStoreError, match="environment"):
        credentials.save_gemini_api_key("vault-secret")
    with pytest.raises(credentials.CredentialStoreError, match="environment"):
        credentials.delete_gemini_api_key()
    assert backend.values == {}


@pytest.mark.parametrize("value", ["", "   ", "bad\nkey", "bad\x00key", "x" * 513])
def test_api_key_validation_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        credentials.validate_api_key(value)


def test_credential_store_unavailable_is_fail_closed(monkeypatch):
    monkeypatch.delenv("PCS_GEMINI_API_KEY", raising=False)

    def unavailable():
        raise credentials.CredentialStoreUnavailable("unavailable")

    monkeypatch.setattr(credentials, "_windows_backend", unavailable)
    assert credentials.get_gemini_api_key() is None
    status = credentials.gemini_key_status()
    assert status["configured"] is False
    assert status["credential_store_available"] is False
    with pytest.raises(credentials.CredentialStoreUnavailable):
        credentials.save_gemini_api_key("test-secret")
