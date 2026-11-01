from __future__ import annotations

import json
from app import diagnostics


def _mock_base(monkeypatch):
    monkeypatch.setattr(diagnostics, "_binary_info", lambda name: {"available": True, "version": "test"})
    monkeypatch.setattr(diagnostics.transcription, "is_available", lambda: True)
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "ollama.exe" if name == "ollama" else None)


def _mock_ollama_http(monkeypatch, payload: bytes = b'{"models":[]}', status: int = 200, error=None):
    state = {}

    class Response:
        def __init__(self):
            self.status = status

        def read(self, _limit):
            return payload

    class Connection:
        def __init__(self, host, port, timeout):
            state["host"] = host
            state["port"] = port
            state["timeout"] = timeout

        def request(self, method, path, headers):
            state.update(method=method, path=path, headers=headers)
            if error:
                raise error

        def getresponse(self):
            return Response()

        def close(self):
            state["closed"] = True

    monkeypatch.setattr(diagnostics.http.client, "HTTPConnection", Connection)
    return state


def test_gemini_diagnostics_reports_configuration_without_exposing_secret(monkeypatch):
    monkeypatch.setenv("PCS_AI_PROVIDER", "gemini")
    monkeypatch.setenv("PCS_GEMINI_MODEL", "gemini-test-model")
    monkeypatch.setenv("PCS_GEMINI_API_KEY", "never-return-this-secret")
    _mock_base(monkeypatch)

    result = diagnostics.runtime_diagnostics()

    assert result["ai"]["configured"] is True
    assert result["ai"]["status"] == "configured"
    assert result["ai"]["model"] == "gemini-test-model"
    assert "never-return-this-secret" not in repr(result)
    assert result["ffmpeg"]["available"] is True
    assert result["transcription"]["installed"] is True
    dependencies = {item["id"]: item for item in result["dependencies"]}
    assert dependencies["ffmpeg"]["available"] is True
    assert dependencies["ffmpeg"]["required"] is True
    assert dependencies["whisper"]["available"] is True
    assert dependencies["ollama"]["required"] is False
    assert dependencies["ffmpeg"]["download_url"].startswith("https://")
    assert result["ai"]["ollama_connection"] == "not_selected"


def test_gemini_diagnostics_identifies_missing_api_key(monkeypatch):
    monkeypatch.setenv("PCS_AI_PROVIDER", "gemini")
    monkeypatch.setenv("PCS_GEMINI_API_KEY", "  ")
    monkeypatch.delenv("PCS_GEMINI_MODEL", raising=False)
    monkeypatch.setattr(diagnostics, "_binary_info", lambda name: {"available": False, "version": None})
    monkeypatch.setattr(diagnostics.transcription, "is_available", lambda: False)
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)

    result = diagnostics.runtime_diagnostics()

    assert result["ai"]["configured"] is False
    assert result["ai"]["status"] == "missing_api_key"
    assert result["transcription"]["installed"] is False
    assert result["ffprobe"]["available"] is False
    dependencies = {item["id"]: item for item in result["dependencies"]}
    assert dependencies["ffmpeg"]["available"] is False
    assert dependencies["whisper"]["available"] is False


def test_ollama_diagnostics_detects_server_and_selected_model(monkeypatch):
    monkeypatch.setenv("PCS_AI_PROVIDER", "ollama")
    monkeypatch.setenv("PCS_OLLAMA_MODEL", "qwen3:8b")
    monkeypatch.setenv("PCS_OLLAMA_URL", "http://127.0.0.1:11434")
    _mock_base(monkeypatch)

    state = _mock_ollama_http(
        monkeypatch,
        json.dumps({"models": [{"name": "qwen3:8b"}, {"name": "llama3:latest"}]}).encode(),
    )
    result = diagnostics.runtime_diagnostics()

    assert state["host"] == "127.0.0.1"
    assert state["port"] == 11434
    assert state["method"] == "GET"
    assert state["path"] == "/api/tags"
    assert state["timeout"] == 2
    assert state["closed"] is True
    assert result["ai"]["configured"] is True
    assert result["ai"]["status"] == "connected_model_available"
    assert result["ai"]["ollama_connection"] == "connected"
    assert result["ai"]["ollama_model_available"] is True
    assert result["ai"]["ollama_models"] == ["qwen3:8b", "llama3:latest"]
    assert "127.0.0.1" not in repr(result)
    ollama = next(item for item in result["dependencies"] if item["id"] == "ollama")
    assert ollama["required"] is False


def test_ollama_diagnostics_reports_missing_model(monkeypatch):
    monkeypatch.setenv("PCS_AI_PROVIDER", "ollama")
    monkeypatch.setenv("PCS_OLLAMA_MODEL", "qwen3:8b")
    monkeypatch.setenv("PCS_OLLAMA_URL", "http://localhost:11434")
    _mock_base(monkeypatch)

    _mock_ollama_http(monkeypatch, b'{"models":[{"name":"llama3:latest"}]}')
    result = diagnostics.runtime_diagnostics()

    assert result["ai"]["status"] == "connected_model_missing"
    assert result["ai"]["ollama_connection"] == "connected"
    assert result["ai"]["ollama_model_available"] is False


def test_ollama_diagnostics_handles_unreachable_server(monkeypatch):
    monkeypatch.setenv("PCS_AI_PROVIDER", "ollama")
    monkeypatch.setenv("PCS_OLLAMA_MODEL", "qwen3:8b")
    monkeypatch.setenv("PCS_OLLAMA_URL", "http://127.0.0.1:11434")
    _mock_base(monkeypatch)

    _mock_ollama_http(monkeypatch, error=OSError("connection refused"))
    result = diagnostics.runtime_diagnostics()

    assert result["ai"]["status"] == "configured_not_connected"
    assert result["ai"]["ollama_connection"] == "unreachable"
    assert result["ai"]["ollama_model_available"] is False


def test_ollama_diagnostics_never_contacts_non_loopback_endpoint(monkeypatch):
    monkeypatch.setenv("PCS_AI_PROVIDER", "ollama")
    monkeypatch.setenv("PCS_OLLAMA_MODEL", "qwen3:8b")
    monkeypatch.setenv("PCS_OLLAMA_URL", "https://example.com")
    _mock_base(monkeypatch)

    state = _mock_ollama_http(monkeypatch)
    result = diagnostics.runtime_diagnostics()

    assert "host" not in state
    assert result["ai"]["status"] == "configured_not_connected"
    assert result["ai"]["ollama_connection"] == "not_tested_unsafe_endpoint"
    assert "example.com" not in repr(result)


def test_diagnostics_rejects_unknown_ai_provider(monkeypatch):
    monkeypatch.setenv("PCS_AI_PROVIDER", "other")
    monkeypatch.setattr(diagnostics, "_binary_info", lambda name: {"available": False, "version": None})
    monkeypatch.setattr(diagnostics.transcription, "is_available", lambda: False)
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)

    result = diagnostics.runtime_diagnostics()

    assert result["ai"]["configured"] is False
    assert result["ai"]["status"] == "unsupported_provider"
    assert result["ai"]["model"] is None



def test_ollama_diagnostics_rejects_malformed_endpoint_port(monkeypatch):
    monkeypatch.setenv("PCS_AI_PROVIDER", "ollama")
    monkeypatch.setenv("PCS_OLLAMA_MODEL", "qwen3:8b")
    monkeypatch.setenv("PCS_OLLAMA_URL", "http://127.0.0.1:not-a-port")
    _mock_base(monkeypatch)

    state = _mock_ollama_http(monkeypatch)
    result = diagnostics.runtime_diagnostics()

    assert "host" not in state
    assert result["ai"]["ollama_connection"] == "not_tested_unsafe_endpoint"
