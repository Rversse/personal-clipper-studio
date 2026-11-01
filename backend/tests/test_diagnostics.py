from __future__ import annotations

from app import diagnostics


def test_gemini_diagnostics_reports_missing_key_without_exposing_secret(monkeypatch):
    monkeypatch.setenv("PCS_AI_PROVIDER", "gemini")
    monkeypatch.setenv("PCS_GEMINI_MODEL", "gemini-test-model")
    monkeypatch.setenv("PCS_GEMINI_API_KEY", "never-return-this-secret")
    monkeypatch.setattr(diagnostics, "_binary_info", lambda name: {"available": True, "version": "test"})
    monkeypatch.setattr(diagnostics.transcription, "is_available", lambda: True)

    result = diagnostics.runtime_diagnostics()

    assert result["ai"] == {
        "provider": "gemini",
        "model": "gemini-test-model",
        "configured": True,
        "status": "configured",
    }
    assert "never-return-this-secret" not in repr(result)
    assert result["ffmpeg"]["available"] is True
    assert result["transcription"]["installed"] is True
    dependencies = {item["id"]: item for item in result["dependencies"]}
    assert dependencies["ffmpeg"]["available"] is True
    assert dependencies["ffmpeg"]["required"] is True
    assert dependencies["whisper"]["available"] is True
    assert dependencies["ollama"]["required"] is False
    assert dependencies["ffmpeg"]["download_url"].startswith("https://")


def test_gemini_diagnostics_identifies_missing_api_key(monkeypatch):
    monkeypatch.setenv("PCS_AI_PROVIDER", "gemini")
    monkeypatch.setenv("PCS_GEMINI_API_KEY", "  ")
    monkeypatch.delenv("PCS_GEMINI_MODEL", raising=False)
    monkeypatch.setattr(diagnostics, "_binary_info", lambda name: {"available": False, "version": None})
    monkeypatch.setattr(diagnostics.transcription, "is_available", lambda: False)

    result = diagnostics.runtime_diagnostics()

    assert result["ai"]["configured"] is False
    assert result["ai"]["status"] == "missing_api_key"
    assert result["transcription"]["installed"] is False
    assert result["ffprobe"]["available"] is False
    dependencies = {item["id"]: item for item in result["dependencies"]}
    assert dependencies["ffmpeg"]["available"] is False
    assert dependencies["whisper"]["available"] is False


def test_ollama_diagnostics_does_not_claim_server_is_connected(monkeypatch):
    monkeypatch.setenv("PCS_AI_PROVIDER", "ollama")
    monkeypatch.setenv("PCS_OLLAMA_MODEL", "qwen3:8b")
    monkeypatch.setenv("PCS_OLLAMA_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(diagnostics, "_binary_info", lambda name: {"available": False, "version": None})
    monkeypatch.setattr(diagnostics.transcription, "is_available", lambda: False)

    result = diagnostics.runtime_diagnostics()

    assert result["ai"]["configured"] is True
    assert result["ai"]["status"] == "configured_not_connection_tested"
    assert "127.0.0.1" not in repr(result)
    ollama = next(item for item in result["dependencies"] if item["id"] == "ollama")
    assert "server" in ollama["description"].lower()
    assert ollama["required"] is False


def test_diagnostics_rejects_unknown_ai_provider(monkeypatch):
    monkeypatch.setenv("PCS_AI_PROVIDER", "other")
    monkeypatch.setattr(diagnostics, "_binary_info", lambda name: {"available": False, "version": None})
    monkeypatch.setattr(diagnostics.transcription, "is_available", lambda: False)

    result = diagnostics.runtime_diagnostics()

    assert result["ai"]["configured"] is False
    assert result["ai"]["status"] == "unsupported_provider"
    assert result["ai"]["model"] is None
