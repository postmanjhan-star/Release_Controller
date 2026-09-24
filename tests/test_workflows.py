from fastapi.testclient import TestClient


def _workflow(client: TestClient, release_id: str) -> dict:
    response = client.get(f"/api/v1/releases/{release_id}/workflow")
    assert response.status_code == 200
    return response.json()


def test_bpmn_definition_is_available(client: TestClient) -> None:
    response = client.get("/api/v1/workflows/release-definition")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert 'id="release_approval"' in response.text
    assert 'id="Task_approval_gate"' in response.text
    assert 'id="Task_execute_deployment"' in response.text


def test_scheduled_bpmn_definition_has_distinct_schedule_and_email_tasks(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/workflows/SCHEDULED/definition")

    assert response.status_code == 200
    assert 'id="SCHEDULE_DEPLOYMENT"' in response.text
    assert '<bpmn:manualTask id="SCHEDULE_DEPLOYMENT"' in response.text
    assert 'id="SEND_EMAIL_NOTIFICATION"' in response.text
    assert 'id="WAIT_SCHEDULED_TIME"' in response.text
    assert 'id="SCHEDULE_TIMER"' in response.text
    assert 'id="CANCEL_SCHEDULE"' in response.text
    assert 'id="START_DEPLOYMENT"' in response.text
    assert 'id="END_CANCELLED"' in response.text


def test_new_release_starts_at_approval_gate(client: TestClient, created_release: dict) -> None:
    workflow = _workflow(client, created_release["id"])

    assert workflow["definition_id"] == "release_approval"
    assert workflow["is_complete"] is False
    assert workflow["current_element_ids"] == ["Task_approval_gate"]
    assert "Flow_start_approval" in workflow["completed_element_ids"]
    approval = next(
        step for step in workflow["steps"] if step["element_id"] == "Task_approval_gate"
    )
    assert approval["state"] == "ACTIVE"


def test_approved_release_moves_through_deployment_to_success(
    client: TestClient, created_release: dict
) -> None:
    release_id = created_release["id"]

    approved = client.post(
        f"/api/v1/releases/{release_id}/approve",
        json={"approved_by": "jhan"},
    )
    assert approved.status_code == 200
    after_approval = _workflow(client, release_id)
    assert after_approval["current_element_ids"] == ["Task_start_deployment"]
    assert {
        "Task_approval_gate",
        "Flow_approval_decision",
        "Flow_decision_approved",
    }.issubset(after_approval["completed_element_ids"])

    started = client.post(f"/api/v1/releases/{release_id}/deployment/start")
    assert started.status_code == 200
    assert started.json()["status"] == "DEPLOYING"
    assert started.json()["deploy_started_at"] is not None
    deploying = _workflow(client, release_id)
    assert deploying["current_element_ids"] == ["Task_execute_deployment"]
    assert "Flow_deployment_started" in deploying["completed_element_ids"]

    finished = client.post(
        f"/api/v1/releases/{release_id}/deployment/finish",
        json={"status": "SUCCESS", "message": "Deployment completed"},
    )
    assert finished.status_code == 200
    assert finished.json()["status"] == "SUCCESS"
    assert finished.json()["deploy_finished_at"] is not None
    completed = _workflow(client, release_id)
    assert completed["is_complete"] is True
    assert completed["current_element_ids"] == []
    assert {
        "Task_execute_deployment",
        "EndEvent_success",
        "Flow_deployment_result",
        "Flow_result_success",
    }.issubset(completed["completed_element_ids"])


def test_rejected_release_completes_rejected_branch(
    client: TestClient, created_release: dict
) -> None:
    release_id = created_release["id"]
    response = client.post(
        f"/api/v1/releases/{release_id}/reject",
        json={"rejected_by": "jhan", "message": "Not ready"},
    )

    assert response.status_code == 200
    workflow = _workflow(client, release_id)
    assert workflow["is_complete"] is True
    assert workflow["current_element_ids"] == []
    assert {
        "Task_approval_gate",
        "EndEvent_rejected",
        "Flow_decision_rejected",
    }.issubset(workflow["completed_element_ids"])
    await_deployment = next(
        step for step in workflow["steps"] if step["element_id"] == "Task_start_deployment"
    )
    assert await_deployment["state"] == "SKIPPED"


def test_failed_deployment_completes_failed_branch(
    client: TestClient, created_release: dict
) -> None:
    release_id = created_release["id"]
    assert (
        client.post(
            f"/api/v1/releases/{release_id}/approve", json={"approved_by": "jhan"}
        ).status_code
        == 200
    )
    assert client.post(f"/api/v1/releases/{release_id}/deployment/start").status_code == 200

    response = client.post(
        f"/api/v1/releases/{release_id}/deployment/finish",
        json={"status": "FAILED", "message": "Health check failed"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "FAILED"
    workflow = _workflow(client, release_id)
    assert workflow["is_complete"] is True
    assert "EndEvent_failed" in workflow["completed_element_ids"]
    assert "Flow_result_failed" in workflow["completed_element_ids"]


def test_deployment_transitions_enforce_expected_status(
    client: TestClient, created_release: dict
) -> None:
    release_id = created_release["id"]

    start = client.post(f"/api/v1/releases/{release_id}/deployment/start")
    finish = client.post(
        f"/api/v1/releases/{release_id}/deployment/finish",
        json={"status": "SUCCESS"},
    )

    assert start.status_code == 409
    assert "expected APPROVED" in start.json()["detail"]
    assert finish.status_code == 409
    assert "expected DEPLOYING" in finish.json()["detail"]


def test_deployment_finish_only_accepts_terminal_status(
    client: TestClient, created_release: dict
) -> None:
    response = client.post(
        f"/api/v1/releases/{created_release['id']}/deployment/finish",
        json={"status": "APPROVED"},
    )

    assert response.status_code == 422


def test_workflow_state_is_persisted_between_requests(
    client: TestClient, created_release: dict
) -> None:
    first = _workflow(client, created_release["id"])
    second = _workflow(client, created_release["id"])

    assert second == first


def test_missing_release_workflow_returns_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/releases/00000000-0000-0000-0000-000000000000/workflow")

    assert response.status_code == 404
    assert response.json() == {"detail": "Release not found"}
