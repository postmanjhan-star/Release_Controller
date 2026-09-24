from fastapi.testclient import TestClient

from app.db.session import get_db


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "release-controller",
        "database": "ok",
    }


def test_health_returns_503_when_database_is_unavailable(client: TestClient) -> None:
    class BrokenSession:
        def execute(self, _statement) -> None:
            raise RuntimeError("database is unavailable")

    def broken_database():
        yield BrokenSession()

    client.app.dependency_overrides[get_db] = broken_database
    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "service": "release-controller",
        "database": "unavailable",
    }
