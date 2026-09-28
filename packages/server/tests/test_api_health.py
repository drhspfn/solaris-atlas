from fastapi.testclient import TestClient

from wuwa_story.api.app import app


def test_health_is_available_without_database() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
