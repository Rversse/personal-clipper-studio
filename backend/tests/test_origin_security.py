import os
import tempfile

# Isolate SQLite state before importing the application module.
os.environ.setdefault("PCS_DATA_DIR", tempfile.mkdtemp(prefix="pcs-security-tests-"))

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
        yield test_client


def test_untrusted_origin_cannot_read_local_api(client):
    response = client.get(
        "/api/health",
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Origin browser tidak diizinkan."


def test_untrusted_origin_cannot_mutate_local_api(client):
    response = client.post(
        "/api/projects",
        headers={
            "Origin": "https://attacker.example",
            "Content-Type": "application/json",
        },
        json={"name": "Cross-origin attempt", "description": ""},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Origin browser tidak diizinkan."


def test_opaque_origin_is_rejected(client):
    response = client.get("/api/health", headers={"Origin": "null"})

    assert response.status_code == 403


def test_trusted_desktop_origin_is_allowed(client):
    response = client.get(
        "/api/health",
        headers={"Origin": "tauri://localhost"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "tauri://localhost"


def test_local_non_browser_client_without_origin_remains_supported(client):
    response = client.get("/api/health")

    assert response.status_code == 200


def test_untrusted_host_is_rejected_even_with_trusted_ui_origin(client):
    response = client.get(
        "/api/health",
        headers={
            "Origin": "tauri://localhost",
            "Host": "attacker.example",
        },
    )

    assert response.status_code == 421
    assert response.json()["detail"] == "Host API lokal tidak diizinkan."


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765"])
def test_trusted_local_api_hosts_are_allowed(client, host):
    response = client.get(
        "/api/health",
        headers={
            "Origin": "tauri://localhost",
            "Host": host,
        },
    )

    assert response.status_code == 200


def test_api_requires_desktop_session_token_when_configured(client, monkeypatch):
    from app import main as main_module

    monkeypatch.setattr(main_module, "_API_SESSION_TOKEN", "test-desktop-session")
    response = client.get("/api/projects")

    assert response.status_code == 401
    assert response.json()["detail"] == "Sesi aplikasi tidak valid."


def test_api_accepts_matching_desktop_session_token(client, monkeypatch):
    from app import main as main_module

    monkeypatch.setattr(main_module, "_API_SESSION_TOKEN", "test-desktop-session")
    response = client.get(
        "/api/projects",
        headers={"Authorization": "Bearer test-desktop-session"},
    )

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_resource_ticket_is_scoped_to_exact_file_path(client, monkeypatch):
    from app import main as main_module

    monkeypatch.setattr(main_module, "_API_SESSION_TOKEN", "test-desktop-session")
    media_id = "00000000-0000-0000-0000-000000000001"
    file_path = f"/api/media/{media_id}/file"
    ticket = main_module.issue_resource_ticket(file_path)

    wrong_resource = client.get(
        f"/api/media/{media_id}/thumbnail?ticket={ticket}",
    )
    correct_resource = client.get(
        f"{file_path}?ticket={ticket}",
    )

    assert wrong_resource.status_code == 401
    # Authentication succeeds for the exact ticketed path; the absent fixture
    # media row then correctly produces 404 rather than an auth failure.
    assert correct_resource.status_code == 404


def test_health_check_remains_available_for_startup_probe(client, monkeypatch):
    from app import main as main_module

    monkeypatch.setattr(main_module, "_API_SESSION_TOKEN", "test-desktop-session")
    response = client.get("/api/health")

    assert response.status_code == 200


def test_desktop_session_token_requires_uuid_v4(monkeypatch):
    from app import main as main_module

    monkeypatch.setattr(main_module, "_API_SESSION_TOKEN", None)
    with pytest.raises(ValueError):
        main_module.configure_session_token("predictable-token")

    token = "9b4f6f1a-8c3e-4f12-9a6d-7e8f9012abcd"
    main_module.configure_session_token(token)
    assert main_module._API_SESSION_TOKEN == token
