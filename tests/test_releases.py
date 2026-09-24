from datetime import datetime

from fastapi.testclient import TestClient


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _payload(
    suffix: str,
    *,
    repository: str = "release-controller",
    branch: str = "main",
    environment: str = "production",
) -> dict[str, str]:
    return {
        "repository": repository,
        "branch": branch,
        "commit_sha": suffix.rjust(40, "0"),
        "environment": environment,
        "message": f"Release {suffix}",
    }


def test_create_release(client: TestClient, release_payload: dict[str, str]) -> None:
    response = client.post("/api/v1/releases", json=release_payload)

    assert response.status_code == 201
    body = response.json()
    assert body["repository"] == release_payload["repository"]
    assert body["status"] == "PENDING"
    assert body["approved_at"] is None
    assert body["rejected_at"] is None
    assert _parse_datetime(body["created_at"]).tzinfo is not None
    assert _parse_datetime(body["updated_at"]).tzinfo is not None


def test_create_release_validates_required_non_empty_fields(client: TestClient) -> None:
    response = client.post(
        "/api/v1/releases",
        json={
            "repository": " ",
            "branch": "main",
            "commit_sha": "abc123",
            "environment": "production",
        },
    )

    assert response.status_code == 422


def test_duplicate_release_returns_conflict(
    client: TestClient, release_payload: dict[str, str]
) -> None:
    first = client.post("/api/v1/releases", json=release_payload)
    second_payload = {**release_payload, "branch": "another-branch"}
    second = client.post("/api/v1/releases", json=second_payload)

    assert first.status_code == 201
    assert second.status_code == 409
    assert "already exists" in second.json()["detail"]


def test_same_commit_can_target_a_different_environment(
    client: TestClient, release_payload: dict[str, str]
) -> None:
    first = client.post("/api/v1/releases", json=release_payload)
    second = client.post(
        "/api/v1/releases",
        json={**release_payload, "environment": "production"},
    )

    assert first.status_code == 201
    assert second.status_code == 201


def test_get_release(client: TestClient, created_release: dict) -> None:
    response = client.get(f"/api/v1/releases/{created_release['id']}")

    assert response.status_code == 200
    assert response.json() == created_release


def test_get_missing_release_returns_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/releases/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json() == {"detail": "Release not found"}


def test_list_releases_filters_orders_and_paginates(client: TestClient) -> None:
    first = client.post(
        "/api/v1/releases",
        json=_payload(
            "1",
            repository="api",
            branch="main",
            environment="pre-production",
        ),
    ).json()
    second = client.post(
        "/api/v1/releases",
        json=_payload(
            "2",
            repository="api",
            branch="release",
            environment="production",
        ),
    ).json()
    third = client.post(
        "/api/v1/releases",
        json=_payload(
            "3",
            repository="worker",
            branch="main",
            environment="production",
        ),
    ).json()
    approved = client.post(
        f"/api/v1/releases/{second['id']}/approve",
        json={"approved_by": "jhan"},
    )
    assert approved.status_code == 200

    all_releases = client.get("/api/v1/releases")
    assert all_releases.status_code == 200
    body = all_releases.json()
    assert body["total"] == 3
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert [item["id"] for item in body["items"]] == [
        third["id"],
        second["id"],
        first["id"],
    ]

    cases = [
        ("repository=api", {first["id"], second["id"]}),
        ("branch=main", {first["id"], third["id"]}),
        ("environment=production", {second["id"], third["id"]}),
        ("status=APPROVED", {second["id"]}),
    ]
    for query, expected_ids in cases:
        response = client.get(f"/api/v1/releases?{query}")
        assert response.status_code == 200
        assert {item["id"] for item in response.json()["items"]} == expected_ids
        assert response.json()["total"] == len(expected_ids)

    page = client.get("/api/v1/releases?limit=1&offset=1")
    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert page.json()["limit"] == 1
    assert page.json()["offset"] == 1
    assert [item["id"] for item in page.json()["items"]] == [second["id"]]


def test_list_releases_rejects_invalid_pagination_and_status(
    client: TestClient,
) -> None:
    assert client.get("/api/v1/releases?limit=101").status_code == 422
    assert client.get("/api/v1/releases?offset=-1").status_code == 422
    assert client.get("/api/v1/releases?status=UNKNOWN").status_code == 422


def test_approve_pending_release(client: TestClient, created_release: dict) -> None:
    response = client.post(
        f"/api/v1/releases/{created_release['id']}/approve",
        json={"approved_by": "jhan"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "APPROVED"
    assert body["approved_by"] == "jhan"
    assert _parse_datetime(body["approved_at"]).tzinfo is not None
    assert body["updated_at"] >= created_release["updated_at"]


def test_approve_non_pending_release_returns_conflict(
    client: TestClient, created_release: dict
) -> None:
    url = f"/api/v1/releases/{created_release['id']}/approve"
    assert client.post(url, json={"approved_by": "jhan"}).status_code == 200

    response = client.post(url, json={"approved_by": "someone-else"})

    assert response.status_code == 409
    assert "expected PENDING" in response.json()["detail"]


def test_reject_pending_release(client: TestClient, created_release: dict) -> None:
    response = client.post(
        f"/api/v1/releases/{created_release['id']}/reject",
        json={
            "rejected_by": "jhan",
            "message": "Production deployment postponed",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "REJECTED"
    assert body["rejected_by"] == "jhan"
    assert body["message"] == "Production deployment postponed"
    assert _parse_datetime(body["rejected_at"]).tzinfo is not None


def test_reject_non_pending_release_returns_conflict(
    client: TestClient, created_release: dict
) -> None:
    approve_url = f"/api/v1/releases/{created_release['id']}/approve"
    assert client.post(approve_url, json={"approved_by": "jhan"}).status_code == 200

    response = client.post(
        f"/api/v1/releases/{created_release['id']}/reject",
        json={"rejected_by": "jhan", "message": "Too late"},
    )

    assert response.status_code == 409


def test_approve_and_reject_missing_release_return_not_found(
    client: TestClient,
) -> None:
    release_id = "00000000-0000-0000-0000-000000000000"
    approve = client.post(f"/api/v1/releases/{release_id}/approve", json={"approved_by": "jhan"})
    reject = client.post(f"/api/v1/releases/{release_id}/reject", json={"rejected_by": "jhan"})

    assert approve.status_code == 404
    assert reject.status_code == 404
