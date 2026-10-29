import pytest
from fastapi.testclient import TestClient

from app.api import health
from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app": "OpsMind",
        "environment": "development",
    }


def test_ready_when_dependencies_up(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(health, "check_database", lambda: True)
    monkeypatch.setattr(health, "check_redis", lambda: True)
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": True, "redis": True}


def test_ready_returns_503_when_database_down(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(health, "check_database", lambda: False)
    monkeypatch.setattr(health, "check_redis", lambda: True)
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"