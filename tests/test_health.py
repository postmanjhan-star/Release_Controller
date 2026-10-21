from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.db.session import get_db


def assert_check_time(value: str, started_at: datetime) -> None:
    checked_at = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert started_at <= checked_at <= datetime.now(timezone.utc)


def test_health_returns_ok(client: TestClient) -> None:
    started_at = datetime.now(timezone.utc).replace(microsecond=0)
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert_check_time(body.pop("time"), started_at)
    assert body == {
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
    started_at = datetime.now(timezone.utc).replace(microsecond=0)
    response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert_check_time(body.pop("time"), started_at)
    assert body == {
        "status": "error",
        "service": "release-controller",
        "database": "unavailable",
    }
