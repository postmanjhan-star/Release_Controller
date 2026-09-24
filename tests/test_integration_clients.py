import httpx
import pytest

from app.integrations.drone.client import DroneClient
from app.integrations.drone.exceptions import (
    DroneAuthenticationError,
    DroneConnectionError,
    DroneNotFoundError,
    DronePromoteError,
    DroneTimeoutError,
    DroneUnexpectedResponseError,
)
from app.integrations.gitea.client import GiteaClient
from app.integrations.gitea.exceptions import (
    GiteaAuthenticationError,
    GiteaConflictError,
    GiteaConnectionError,
    GiteaTimeoutError,
    GiteaUnexpectedResponseError,
)


def test_drone_client_maps_successful_responses(monkeypatch) -> None:
    requests: list[tuple[str, str, dict]] = []
    responses = [
        [{"number": 12, "status": "success", "event": "push", "after": "a" * 40}],
        {"slug": "team/api", "default_branch": "main"},
        {"number": 12, "status": "success", "event": "push"},
        {"number": 99, "status": "pending", "event": "promote"},
        {"login": "release-bot"},
    ]

    def fake_request(method: str, url: str, **kwargs) -> httpx.Response:
        requests.append((method, url, kwargs))
        return httpx.Response(200, json=responses.pop(0))

    monkeypatch.setattr(httpx, "request", fake_request)
    client = DroneClient("http://drone.test/", "secret", timeout=3)

    assert client.list_builds("team", "api", limit=5)[0].number == 12
    assert client.get_repository("team", "api").slug == "team/api"
    assert client.get_build("team", "api", 12).is_promotable()
    assert client.promote_build("team", "api", 12, "staging").number == 99
    assert client.status() is True
    method, url, options = requests[0]
    assert (method, url) == ("GET", "http://drone.test/api/repos/team/api/builds")
    assert options["headers"] == {"Authorization": "Bearer secret"}
    assert options["params"] == {"limit": 5}
    # Reads and writes carry separate budgets: a poll must not wait as long as a
    # promote, and connecting must not wait as long as either.
    read_timeout = options["timeout"]
    assert read_timeout.read == 3
    assert read_timeout.connect == 3
    promote_timeout = requests[3][2]["timeout"]
    assert promote_timeout.read == 30
    assert promote_timeout.connect == 3


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(401, json={}), DroneAuthenticationError),
        (httpx.Response(404, json={}), DroneNotFoundError),
        (httpx.Response(500, json={}), DroneConnectionError),
        (httpx.Response(409, json={}), DroneUnexpectedResponseError),
        (httpx.Response(200, content=b"not-json"), DroneUnexpectedResponseError),
    ],
)
def test_drone_client_classifies_http_failures(monkeypatch, response, expected) -> None:
    monkeypatch.setattr(httpx, "request", lambda *args, **kwargs: response)

    with pytest.raises(expected):
        DroneClient("http://drone.test", "secret").get_build("team", "api", 10)


def test_drone_client_classifies_network_failures(monkeypatch) -> None:
    request = httpx.Request("GET", "http://drone.test/api/user")
    client = DroneClient("http://drone.test", "secret")

    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request)),
    )
    with pytest.raises(DroneTimeoutError):
        client.status()

    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("down", request=request)),
    )
    with pytest.raises(DroneConnectionError):
        client.status()


def test_drone_promote_translates_missing_and_invalid_builds(monkeypatch) -> None:
    client = DroneClient("http://drone.test", "secret")
    monkeypatch.setattr(httpx, "request", lambda *args, **kwargs: httpx.Response(404, json={}))
    with pytest.raises(DronePromoteError):
        client.promote_build("team", "api", 10, "staging")

    monkeypatch.setattr(httpx, "request", lambda *args, **kwargs: httpx.Response(200, json={}))
    with pytest.raises(DronePromoteError):
        client.promote_build("team", "api", 10, "staging")


@pytest.mark.parametrize(
    ("behavior", "expected"),
    [
        (lambda: httpx.Response(503, json={}), GiteaConnectionError),
        (lambda: httpx.Response(401, json={}), GiteaUnexpectedResponseError),
        (lambda: httpx.Response(200, content=b"not-json"), GiteaUnexpectedResponseError),
        (lambda: httpx.Response(200, json={"status": "fail"}), GiteaUnexpectedResponseError),
    ],
)
def test_gitea_client_rejects_unhealthy_responses(monkeypatch, behavior, expected) -> None:
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: behavior())

    with pytest.raises(expected):
        GiteaClient("http://gitea.test").health()


def test_gitea_client_classifies_network_failures(monkeypatch) -> None:
    request = httpx.Request("GET", "http://gitea.test/api/healthz")
    client = GiteaClient("http://gitea.test")

    monkeypatch.setattr(
        httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request)),
    )
    with pytest.raises(GiteaTimeoutError):
        client.health()

    monkeypatch.setattr(
        httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("down", request=request)),
    )
    with pytest.raises(GiteaConnectionError):
        client.health()


def test_gitea_release_client_uses_token_and_deployed_commit(monkeypatch) -> None:
    requests: list[tuple[str, str, dict]] = []
    responses = [
        {"full_name": "team/api", "html_url": "http://gitea/team/api"},
        {"name": "v1.8.0", "commit": {"sha": "a" * 40}},
        {"id": 8, "tag_name": "v1.8.0", "name": "v1.8.0"},
        {"id": 9, "tag_name": "v1.9.0", "name": "v1.9.0", "html_url": "http://release"},
    ]

    def fake_request(method: str, url: str, **kwargs) -> httpx.Response:
        requests.append((method, url, kwargs))
        return httpx.Response(200, json=responses.pop(0))

    monkeypatch.setattr(httpx, "request", fake_request)
    client = GiteaClient("http://gitea.test/", "top-secret", timeout=3)

    assert client.get_repository("team", "api").full_name == "team/api"
    assert client.get_tag("team", "api", "v1.8.0").commit_sha == "a" * 40
    assert client.get_release_by_tag("team", "api", "v1.8.0").id == 8
    created = client.create_release("team", "api", "v1.9.0", "b" * 40, "v1.9.0", "notes")

    assert created.id == 9
    assert requests[-1][2]["json"]["target_commitish"] == "b" * 40
    assert requests[-1][2]["headers"]["Authorization"] == "token top-secret"


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(401, GiteaAuthenticationError), (409, GiteaConflictError)],
)
def test_gitea_release_client_classifies_auth_and_conflicts(
    monkeypatch, status_code: int, expected: type[Exception]
) -> None:
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: httpx.Response(status_code, json={}),
    )
    with pytest.raises(expected):
        GiteaClient("http://gitea.test", "secret").create_release(
            "team", "api", "v1.8.0", "a" * 40, "v1.8.0", ""
        )
