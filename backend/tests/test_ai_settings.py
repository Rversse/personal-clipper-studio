from __future__ import annotations

from fastapi.testclient import TestClient

from app import ai_settings, credentials
from app.main import app


def test_ai_settings_never_returns_gemini_secret(monkeypatch):
    monkeypatch.setattr(
        credentials,
        "gemini_key_status",
        lambda: {
            "configured": True,
            "source": "windows_credential_manager",
            "credential_store_available": True,
            "managed_by_environment": False,
        },
    )
    with TestClient(app) as client:
        response = client.get("/api/settings/ai")

    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert "api_key" not in response.json()
    assert "secret" not in response.text


def test_save_gemini_key_returns_status_only(monkeypatch):
    stored = []
    monkeypatch.setattr(credentials, "save_gemini_api_key", stored.append)
    monkeypatch.setattr(
        ai_settings,
        "_settings_payload",
        lambda: {"configured": True, "source": "windows_credential_manager"},
    )
    with TestClient(app) as client:
        response = client.put(
            "/api/settings/ai/gemini-key",
            json={"api_key": "never-return-this-secret"},
        )

    assert response.status_code == 200
    assert stored == ["never-return-this-secret"]
    assert "never-return-this-secret" not in response.text
    assert response.json()["configured"] is True


def test_delete_gemini_key(monkeypatch):
    deleted = []
    monkeypatch.setattr(credentials, "delete_gemini_api_key", lambda: deleted.append(True))
    monkeypatch.setattr(ai_settings, "_settings_payload", lambda: {"configured": False})
    with TestClient(app) as client:
        response = client.delete("/api/settings/ai/gemini-key")

    assert response.status_code == 200
    assert deleted == [True]
    assert response.json()["configured"] is False


def test_gemini_test_requires_configured_key_without_network(monkeypatch):
    monkeypatch.setattr(credentials, "get_gemini_api_key", lambda: None)
    called = []
    monkeypatch.setattr(ai_settings, "_test_gemini_key", lambda *args: called.append(args))
    with TestClient(app) as client:
        response = client.post("/api/settings/ai/gemini-test")

    assert response.status_code == 409
    assert called == []


def test_gemini_test_checks_model_access_without_returning_key(monkeypatch):
    monkeypatch.setattr(credentials, "get_gemini_api_key", lambda: "private-test-key")
    checked = []
    monkeypatch.setattr(
        ai_settings,
        "_test_gemini_key",
        lambda key, model: checked.append((key, model)) or {
            "success": True,
            "status": "connected",
            "message": "API key valid dan model dapat diakses.",
        },
    )
    with TestClient(app) as client:
        response = client.post("/api/settings/ai/gemini-test")

    assert response.status_code == 200
    assert checked == [("private-test-key", "gemini-3.8-flash")]
    assert "private-test-key" not in response.text
    assert response.json()["success"] is True


def test_gemini_test_request_uses_header_and_never_puts_key_in_url(monkeypatch):
    state = {}

    class Response:
        status = 200

        def read(self, _limit):
            return b'{"name":"models/gemini-test"}'

    class Connection:
        def __init__(self, host, timeout, context):
            state.update(host=host, timeout=timeout, context=context)

        def request(self, method, path, headers):
            state.update(method=method, path=path, headers=headers)

        def getresponse(self):
            return Response()

        def close(self):
            state["closed"] = True

    monkeypatch.setattr(ai_settings.http.client, "HTTPSConnection", Connection)
    result = ai_settings._test_gemini_key("header-only-secret", "gemini-test")

    assert result["success"] is True
    assert state["host"] == "generativelanguage.googleapis.com"
    assert state["method"] == "GET"
    assert state["path"] == "/v1beta/models/gemini-test"
    assert state["headers"]["x-goog-api-key"] == "header-only-secret"
    assert "header-only-secret" not in state["path"]
    assert state["closed"] is True



def test_invalid_gemini_key_is_not_reflected_in_validation_response():
    secret = "too-long-private-key-" + ("x" * 600)
    with TestClient(app) as client:
        response = client.put(
            "/api/settings/ai/gemini-key",
            json={"api_key": secret},
        )

    assert response.status_code == 422
    assert secret not in response.text


def test_gemini_model_metadata_error_statuses_are_classified(monkeypatch):
    class Response:
        def __init__(self, status):
            self.status = status

        def read(self, _limit):
            return b'{"error":{"message":"private provider detail"}}'

    class Connection:
        status = 401

        def __init__(self, host, timeout, context):
            pass

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            return Response(self.status)

        def close(self):
            pass

    monkeypatch.setattr(ai_settings.http.client, "HTTPSConnection", Connection)
    cases = {
        401: "authentication_failed",
        403: "authentication_failed",
        404: "model_not_found",
        429: "rate_limited",
        503: "provider_error",
    }
    for http_status, expected in cases.items():
        Connection.status = http_status
        result = ai_settings._test_gemini_key("private-test-key", "gemini-test")
        assert result["status"] == expected
        assert "private provider detail" not in result["message"]


def test_gemini_model_metadata_network_error_is_safe(monkeypatch):
    class Connection:
        def __init__(self, host, timeout, context):
            pass

        def request(self, *_args, **_kwargs):
            raise OSError("sensitive transport detail")

        def close(self):
            pass

    monkeypatch.setattr(ai_settings.http.client, "HTTPSConnection", Connection)
    result = ai_settings._test_gemini_key("private-test-key", "gemini-test")

    assert result["success"] is False
    assert result["status"] == "unreachable"
    assert "sensitive transport detail" not in result["message"]



def test_gemini_key_request_body_is_bounded_and_not_reflected():
    secret = "private-" + ("z" * 5000)
    with TestClient(app) as client:
        response = client.put(
            "/api/settings/ai/gemini-key",
            content='{"api_key":"' + secret + '"}',
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert secret not in response.text


def test_gemini_key_request_rejects_non_string_without_echoing_payload():
    secret = "private-non-string-secret"
    with TestClient(app) as client:
        response = client.put(
            "/api/settings/ai/gemini-key",
            json={"api_key": {"unexpected": secret}},
        )

    assert response.status_code == 422
    assert secret not in response.text



def test_gemini_test_rejects_cross_origin_browser_request(monkeypatch):
    called = []
    monkeypatch.setattr(credentials, "get_gemini_api_key", lambda: called.append(True))
    with TestClient(app) as client:
        response = client.post(
            "/api/settings/ai/gemini-test",
            headers={"Origin": "https://attacker.example"},
        )

    assert response.status_code == 403
    assert called == []
    assert "attacker.example" not in response.text



def test_gemini_test_accepts_tauri_origin(monkeypatch):
    monkeypatch.setattr(credentials, "get_gemini_api_key", lambda: "test-key")
    monkeypatch.setattr(
        ai_settings,
        "_test_gemini_key",
        lambda *_args: {"success": True, "status": "connected", "message": "ok"},
    )
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.post(
            "/api/settings/ai/gemini-test",
            headers={"Origin": "http://tauri.localhost"},
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
