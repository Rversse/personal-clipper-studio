import os
import tempfile

# Isolate SQLite state before importing the application module.
os.environ.setdefault("PCS_DATA_DIR", tempfile.mkdtemp(prefix="pcs-security-tests-"))

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
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
