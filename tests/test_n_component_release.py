"""Tests for v3.0 step 3: a release covers N components, in their own order.

The two-component behaviour these generalise is asserted by the existing v2.2
suite, which still passes unchanged -- that is the equivalence evidence.  What is
here is everything the old shape could not express: one component, three, a
subset, and a component that deploys but does not publish.
"""

import pytest
from fastapi.testclient import TestClient

from app.db.models.registry import ProjectComponent
from app.infrastructure.di.injection import (
    get_drone_client,
    get_drone_clients,
    get_gitea_clients,
)
from app.integrations.drone.schemas import DroneBuild
from app.services.upstream_clients import FixedDroneClients, FixedGiteaClients
from tests.conftest import registered_component

GATEWAY_REPO = "gateway"


def build(number: int, status: str = "success", **extra) -> DroneBuild:
    return DroneBuild(
        number=number,
        status=status,
        event=extra.pop("event", "push"),
        branch="main",
        commit_sha=f"{number:040d}",
        commit_message="ready",
        author="tester",
        **extra,
    )


class ScriptedDrone:
    """Promotes anything; the promotion build succeeds unless told otherwise.

    A promotion only becomes a failure when the deployment is refreshed and the
    promotion build is read back, so the failure has to live in get_build too --
    which is exactly how a real Drone reports it.
    """

    def __init__(self) -> None:
        self.promotions: list[tuple[str, int, str]] = []
        self.fail_repos: set[str] = set()
        self._next = 900
        self._promotion_builds: dict[tuple[str, int], str] = {}

    def get_build(self, owner: str, repo: str, number: int) -> DroneBuild:
        return build(number, self._promotion_builds.get((repo, number), "success"))

    def get_repository(self, owner: str, repo: str) -> dict:
        return {"name": repo, "namespace": owner, "default_branch": "main"}

    def list_builds(self, owner: str, repo: str, limit: int = 20) -> list[DroneBuild]:
        return [build(1)]

    def promote_build(self, owner: str, repo: str, number: int, target: str) -> DroneBuild:
        self.promotions.append((repo, number, target))
        self._next += 1
        status = "failure" if repo in self.fail_repos else "success"
        self._promotion_builds[(repo, self._next)] = status
        return build(self._next, "running", event="promote", parent=number, target=target)


class StubGitea:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, str]] = []
        # Keyed by repository as well as tag: two repositories may each hold
        # their own v1.0.0, and a stub that forgets that reports a false conflict.
        self.tags: dict[tuple[str, str, str], str] = {}

    def get_repository(self, owner: str, repo: str) -> dict:
        return {"full_name": f"{owner}/{repo}"}

    def get_tag(self, owner: str, repo: str, tag: str):
        from app.integrations.gitea.exceptions import GiteaNotFoundError

        if (owner, repo, tag) not in self.tags:
            raise GiteaNotFoundError("no such tag")
        from app.integrations.gitea.schemas import GiteaTag

        return GiteaTag(name=tag, commit_sha=self.tags[(owner, repo, tag)])

    def get_release_by_tag(self, owner: str, repo: str, tag: str):
        from app.integrations.gitea.schemas import GiteaRelease

        return GiteaRelease(id=1, name=tag, tag_name=tag, html_url=f"http://g/{repo}/{tag}")

    def create_release(self, owner, repo, tag, sha, name, notes, draft, prerelease):
        from app.integrations.gitea.schemas import GiteaRelease

        self.created.append((owner, repo, tag))
        self.tags[(owner, repo, tag)] = sha
        return GiteaRelease(id=1, name=tag, tag_name=tag, html_url=f"http://g/{repo}/{tag}")


@pytest.fixture
def drone(client: TestClient) -> ScriptedDrone:
    scripted = ScriptedDrone()
    client.app.dependency_overrides[get_drone_client] = lambda: scripted
    client.app.dependency_overrides[get_drone_clients] = lambda: FixedDroneClients(scripted)
    return scripted


@pytest.fixture
def gitea(client: TestClient) -> StubGitea:
    stub = StubGitea()
    client.app.dependency_overrides[get_gitea_clients] = lambda: FixedGiteaClients(stub)
    return stub


def add_gateway(client: TestClient, *, position: int = 3, publish: bool = True) -> str:
    """A third component, so the release stops being a pair by construction."""
    response = client.post(
        "/api/v1/projects/default/components",
        json={
            "key": "gateway",
            "drone_owner": "102573",
            "drone_repo": GATEWAY_REPO,
            "gitea_owner": "102573" if publish else None,
            "gitea_repo": GATEWAY_REPO if publish else None,
            "publish_enabled": publish,
            "position": position,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def promote(client: TestClient, components: list[dict], **extra) -> dict:
    response = client.post("/api/v1/releases/promote", json={"components": components, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def refresh(client: TestClient, release_id: str) -> dict:
    response = client.post(f"/api/v1/releases/{release_id}/refresh")
    assert response.status_code == 200, response.text
    return response.json()


def statuses(bundle: dict) -> dict[str, str]:
    return {item["component"]: item["status"] for item in bundle["deployments"]}


# --------------------------------------------------------------------------- #
# N components
# --------------------------------------------------------------------------- #


def test_a_release_of_one_component_is_a_release(client: TestClient, drone: ScriptedDrone) -> None:
    bundle = promote(client, [{"key": "backend", "build_number": 456}])

    assert bundle["selected_component_keys"] == "backend"
    assert len(bundle["deployments"]) == 1
    assert drone.promotions == [("soda", 456, "production")]

    finished = refresh(client, bundle["id"])
    assert finished["status"] == "SUCCESS"


def test_three_components_deploy_one_at_a_time_in_position_order(
    client: TestClient, drone: ScriptedDrone
) -> None:
    add_gateway(client)

    bundle = promote(
        client,
        [
            # Listed out of order on purpose: position decides, not the caller.
            {"key": "gateway", "build_number": 3},
            {"key": "frontend", "build_number": 123},
            {"key": "backend", "build_number": 456},
        ],
    )

    assert bundle["selected_component_keys"] == "backend,frontend,gateway"
    # Only the first component is sent upstream; the rest are still WAITING.
    assert drone.promotions == [("soda", 456, "production")]
    assert statuses(bundle) == {
        "backend": "DEPLOYING",
        "frontend": "WAITING",
        "gateway": "WAITING",
    }

    after_first = refresh(client, bundle["id"])
    assert [repo for repo, _n, _t in drone.promotions] == ["soda", "SMT-Assistant"]
    assert statuses(after_first)["backend"] == "SUCCESS"

    refresh(client, bundle["id"])
    assert [repo for repo, _n, _t in drone.promotions] == ["soda", "SMT-Assistant", GATEWAY_REPO]

    finished = refresh(client, bundle["id"])
    assert finished["status"] == "SUCCESS"
    assert set(statuses(finished).values()) == {"SUCCESS"}


def test_a_release_may_cover_a_subset_of_the_project(
    client: TestClient, drone: ScriptedDrone
) -> None:
    """Shipping a fix to one service without redeploying the others."""
    add_gateway(client)

    bundle = promote(
        client,
        [
            {"key": "backend", "build_number": 456},
            {"key": "gateway", "build_number": 3},
        ],
    )

    assert bundle["selected_component_keys"] == "backend,gateway"
    assert {item["component"] for item in bundle["deployments"]} == {"backend", "gateway"}


def test_the_first_component_failing_fails_the_release_and_cancels_the_rest(
    client: TestClient, drone: ScriptedDrone
) -> None:
    add_gateway(client)
    drone.fail_repos.add("soda")

    bundle = promote(
        client,
        [
            {"key": "backend", "build_number": 456},
            {"key": "frontend", "build_number": 123},
            {"key": "gateway", "build_number": 3},
        ],
    )
    finished = refresh(client, bundle["id"])

    assert finished["status"] == "FAILED"
    assert statuses(finished) == {
        "backend": "FAILED",
        "frontend": "CANCELLED",
        "gateway": "CANCELLED",
    }
    # Nothing after the failure was sent upstream.
    assert [repo for repo, _n, _t in drone.promotions] == ["soda"]


def test_a_later_component_failing_is_a_partial_failure(
    client: TestClient, drone: ScriptedDrone
) -> None:
    """Something already reached production, so this is not a clean failure."""
    add_gateway(client)
    drone.fail_repos.add(GATEWAY_REPO)

    bundle = promote(
        client,
        [
            {"key": "backend", "build_number": 456},
            {"key": "frontend", "build_number": 123},
            {"key": "gateway", "build_number": 3},
        ],
    )
    for _ in range(4):
        finished = refresh(client, bundle["id"])

    assert finished["status"] == "PARTIAL_FAILURE"
    assert statuses(finished) == {
        "backend": "SUCCESS",
        "frontend": "SUCCESS",
        "gateway": "FAILED",
    }


def test_a_partly_failed_release_retries_only_what_failed(
    client: TestClient, drone: ScriptedDrone
) -> None:
    add_gateway(client)
    drone.fail_repos.add(GATEWAY_REPO)
    bundle = promote(
        client,
        [
            {"key": "backend", "build_number": 456},
            {"key": "gateway", "build_number": 3},
        ],
    )
    for _ in range(3):
        refresh(client, bundle["id"])
    assert refresh(client, bundle["id"])["status"] == "PARTIAL_FAILURE"

    drone.fail_repos.clear()
    retried = client.post(f"/api/v1/releases/{bundle['id']}/retry")
    assert retried.status_code == 200

    # The successful component is not promoted a second time.
    assert [repo for repo, _n, _t in drone.promotions] == ["soda", GATEWAY_REPO, GATEWAY_REPO]
    finished = refresh(client, bundle["id"])
    assert finished["status"] == "SUCCESS"


# --------------------------------------------------------------------------- #
# selection rules
# --------------------------------------------------------------------------- #


def test_the_v2_2_body_still_works(client: TestClient, drone: ScriptedDrone) -> None:
    """Two build numbers named after the two components there used to be."""
    response = client.post(
        "/api/v1/releases/promote",
        json={"frontend_build_number": 123, "backend_build_number": 456},
    )

    assert response.status_code == 201
    assert response.json()["selected_component_keys"] == "backend,frontend"


def test_a_release_of_nothing_is_refused(client: TestClient) -> None:
    response = client.post("/api/v1/releases/promote", json={"target": "production"})

    assert response.status_code == 422


def test_the_same_component_twice_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/v1/releases/promote",
        json={
            "components": [
                {"key": "backend", "build_number": 1},
                {"key": "backend", "build_number": 2},
            ]
        },
    )

    assert response.status_code == 422


def test_an_unknown_component_says_what_the_project_has(client: TestClient) -> None:
    response = client.post(
        "/api/v1/releases/promote",
        json={"components": [{"key": "nope", "build_number": 1}]},
    )

    assert response.status_code == 404
    assert "backend, frontend" in response.json()["detail"]


def test_an_inactive_component_cannot_be_released(client: TestClient) -> None:
    with client.session_factory() as db:
        component = registered_component(db, "frontend")
        component.is_active = False
        db.commit()

    response = client.post(
        "/api/v1/releases/promote",
        json={"components": [{"key": "frontend", "build_number": 123}]},
    )

    assert response.status_code == 422
    assert "inactive" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# publishing across N components
# --------------------------------------------------------------------------- #


def _succeed(client: TestClient, bundle_id: str) -> dict:
    for _ in range(5):
        bundle = refresh(client, bundle_id)
        if bundle["status"] in {"SUCCESS", "FAILED", "PARTIAL_FAILURE"}:
            return bundle
    return bundle


def test_publishing_covers_every_component_that_publishes(
    client: TestClient, drone: ScriptedDrone, gitea: StubGitea
) -> None:
    add_gateway(client)
    bundle = promote(
        client,
        [
            {"key": "backend", "build_number": 456},
            {"key": "frontend", "build_number": 123},
            {"key": "gateway", "build_number": 3},
        ],
    )
    assert _succeed(client, bundle["id"])["status"] == "SUCCESS"

    response = client.post(
        f"/api/v1/releases/{bundle['id']}/publish",
        json={"version": "v9.0.0", "name": "v9", "draft": False, "prerelease": False},
    )

    assert response.status_code == 200
    assert response.json()["publish_status"] == "PUBLISHED"
    assert [repo for _owner, repo, _tag in gitea.created] == ["soda", "SMT-Assistant", GATEWAY_REPO]


def test_a_component_that_does_not_publish_is_not_counted_as_a_failure(
    client: TestClient, drone: ScriptedDrone, gitea: StubGitea
) -> None:
    """Otherwise a release containing one would be PARTIAL_FAILURE forever."""
    add_gateway(client, publish=False)
    bundle = promote(
        client,
        [
            {"key": "backend", "build_number": 456},
            {"key": "gateway", "build_number": 3},
        ],
    )
    assert _succeed(client, bundle["id"])["status"] == "SUCCESS"

    response = client.post(
        f"/api/v1/releases/{bundle['id']}/publish",
        json={"version": "v9.1.0", "name": "v9.1", "draft": False, "prerelease": False},
    )

    assert response.status_code == 200
    assert response.json()["publish_status"] == "PUBLISHED"
    # The gateway was skipped rather than attempted and failed.
    assert [repo for _owner, repo, _tag in gitea.created] == ["soda"]
    published = {
        item["component"]: item["publish_status"] for item in response.json()["deployments"]
    }
    assert published["backend"] == "PUBLISHED"
    assert published["gateway"] == "NOT_PUBLISHED"


def test_a_release_where_nothing_publishes_says_so(
    client: TestClient, drone: ScriptedDrone, gitea: StubGitea
) -> None:
    with client.session_factory() as db:
        for key in ("backend", "frontend"):
            registered_component(db, key).publish_enabled = False
        db.commit()

    bundle = promote(client, [{"key": "backend", "build_number": 456}])
    assert _succeed(client, bundle["id"])["status"] == "SUCCESS"

    response = client.post(
        f"/api/v1/releases/{bundle['id']}/publish",
        json={"version": "v9.2.0", "name": "v9.2", "draft": False, "prerelease": False},
    )

    assert response.status_code == 409
    assert "nothing to do" in response.json()["detail"]


def test_a_component_beyond_frontend_and_backend_gets_the_generic_stages(
    client: TestClient, drone: ScriptedDrone
) -> None:
    """The stage names stop being per-component; the event says which one it was."""
    add_gateway(client)
    bundle = promote(client, [{"key": "gateway", "build_number": 3}])

    events = client.get(f"/api/v1/releases/{bundle['id']}/events").json()["items"]
    stages = {event["stage"] for event in events}

    assert "VALIDATE_COMPONENT" in stages
    assert "PROMOTE_COMPONENT" in stages
    assert {event["component"] for event in events if event["deployment_id"]} == {"gateway"}


def test_positions_stay_dense_when_a_component_is_added_in_the_middle(
    client: TestClient,
) -> None:
    """The order a release runs in is the order the components are listed in."""
    add_gateway(client, position=2)

    project = client.get("/api/v1/projects/default").json()

    assert [c["key"] for c in project["components"]] == ["backend", "gateway", "frontend"]
    assert [c["position"] for c in project["components"]] == [1, 2, 3]


def test_deployment_order_follows_the_component_positions(
    client: TestClient, drone: ScriptedDrone
) -> None:
    add_gateway(client, position=1)

    bundle = promote(
        client,
        [
            {"key": "backend", "build_number": 456},
            {"key": "gateway", "build_number": 3},
        ],
    )

    assert bundle["selected_component_keys"] == "gateway,backend"
    assert drone.promotions == [(GATEWAY_REPO, 3, "production")]


def test_a_component_with_its_own_target_uses_it(client: TestClient, drone: ScriptedDrone) -> None:
    """The monorepo case, end to end: one repo, two components, two targets."""
    with client.session_factory() as db:
        backend = registered_component(db, "backend")
        project = backend.project
        project.components.append(
            ProjectComponent(
                key="web",
                display_name="Web",
                position=9,
                drone_owner=backend.drone_owner,
                drone_repo=backend.drone_repo,
                gitea_owner=backend.gitea_owner,
                gitea_repo=backend.gitea_repo,
                promote_target_override="production-web",
                tag_prefix="web-",
            )
        )
        db.commit()

    bundle = promote(
        client,
        [
            {"key": "backend", "build_number": 456},
            {"key": "web", "build_number": 456},
        ],
    )

    # Same repository, same build, two different promotion slots.
    assert len(bundle["deployments"]) == 2
    targets = {item["component"]: item["target"] for item in bundle["deployments"]}
    assert targets == {"backend": "production", "web": "production-web"}


# --------------------------------------------------------------------------- #
# project-scoped build endpoints
# --------------------------------------------------------------------------- #


def test_builds_can_be_listed_for_any_component(client: TestClient, drone: ScriptedDrone) -> None:
    """The /drone/{component}/builds pair cannot name a gateway; this can."""
    add_gateway(client)

    response = client.get("/api/v1/projects/default/components/gateway/builds")

    assert response.status_code == 200
    assert response.json()["component"] == "gateway"
    assert response.json()["repository"]["slug"] == f"102573/{GATEWAY_REPO}"


def test_any_component_can_be_deployed_on_its_own(client: TestClient, drone: ScriptedDrone) -> None:
    add_gateway(client)

    response = client.post("/api/v1/projects/default/components/gateway/builds/3/promote", json={})

    assert response.status_code == 201
    assert response.json()["component"] == "gateway"
    assert response.json()["release_bundle_id"] is None
    assert drone.promotions == [(GATEWAY_REPO, 3, "production")]


def test_an_inactive_component_cannot_be_deployed_on_its_own(client: TestClient) -> None:
    add_gateway(client)
    client.patch("/api/v1/projects/default/components/gateway", json={"is_active": False})

    response = client.post("/api/v1/projects/default/components/gateway/builds/3/promote", json={})

    assert response.status_code == 422
    assert "inactive" in response.json()["detail"]


def test_an_unknown_component_is_a_404_from_the_project_endpoints(client: TestClient) -> None:
    assert client.get("/api/v1/projects/default/components/nope/builds").status_code == 404
    assert (
        client.post(
            "/api/v1/projects/default/components/nope/builds/1/promote", json={}
        ).status_code
        == 404
    )


def test_history_can_be_filtered_by_a_component_beyond_the_original_two(
    client: TestClient, drone: ScriptedDrone
) -> None:
    """The filter took a two-valued enum, so a third component was unfilterable.

    Nothing failed loudly: asking for ?component=gateway was a 422, which reads
    like a malformed request rather than "this application cannot express your
    component".
    """
    add_gateway(client)
    promoted = client.post("/api/v1/projects/default/components/gateway/builds/3/promote", json={})
    assert promoted.status_code == 201, promoted.text
    client.post("/api/v1/projects/default/components/backend/builds/456/promote", json={})

    filtered = client.get("/api/v1/deployments", params={"component": "gateway"})

    assert filtered.status_code == 200
    assert [item["component"] for item in filtered.json()["items"]] == ["gateway"]
    # And a key no project uses is an empty result, not an error.
    unknown = client.get("/api/v1/deployments", params={"component": "no-such-component"})
    assert unknown.status_code == 200
    assert unknown.json()["items"] == []
