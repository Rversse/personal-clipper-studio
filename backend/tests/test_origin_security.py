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
