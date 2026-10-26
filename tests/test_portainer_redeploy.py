import errno
import io
import json
import socket
import ssl
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from devtools import portainer_redeploy as deployment


@pytest.fixture
def portainer(monkeypatch):
    stack = {
        "Id": 7,
        "EndpointId": 3,
        "GitConfig": {
            "ReferenceName": "refs/heads/deploy",
            "Authentication": {"Username": "git-reader", "Password": "git-secret"},
        },
        "Env": [{"name": "APP_SECRET_KEY", "value": "app-secret"}],
    }
    replies = {"/api/endpoints/3": (200, {"Id": 3}), "/api/stacks/7": (200, stack)}
    requests = []

    def request(req, **options):
        requests.append(req)
        path = req.full_url.split("portainer.test", 1)[1]
        response = replies.get(path, (200, stack))
        if isinstance(response, Exception):
            raise response
        status, payload = response
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        if status >= 300:
            raise HTTPError(req.full_url, status, "request failed", {}, io.BytesIO(body))
        return io.BytesIO(body)

    monkeypatch.setattr(deployment, "build_opener", lambda *handlers: SimpleNamespace(open=request))
    return SimpleNamespace(
        environment={
            "PORTAINER_URL": "https://portainer.test",
            "PORTAINER_API_TOKEN": "api-secret",
            "PORTAINER_STACK_ID": "7",
            "PORTAINER_ENDPOINT_ID": "3",
        },
        stack=stack,
        replies=replies,
        requests=requests,
    )


@pytest.mark.parametrize("suffix", ["", "/", "/api", "/api/"])
def test_redeploy_normalizes_base_and_preserves_stack_configuration(portainer, suffix, capsys):
    portainer.environment["PORTAINER_URL"] += suffix
    deployment.redeploy(portainer.environment)
    assert [req.get_method() for req in portainer.requests] == ["GET", "GET", "PUT"]
    request = portainer.requests[-1]
    assert request.full_url == "https://portainer.test/api/stacks/7/git/redeploy?endpointId=3"
    assert request.get_header("X-api-key") == "api-secret"
    payload = json.loads(request.data)
    assert payload == {
        "Prune": False,
        "RepositoryReferenceName": "refs/heads/deploy",
        "Env": portainer.stack["Env"],
        "RepositoryAuthentication": True,
        "RepositoryUsername": "git-reader",
    }
    output = capsys.readouterr().out
    assert "Portainer redeploy request completed." in output
    for secret in ("api-secret", "git-secret", "app-secret"):
        assert secret not in output


@pytest.mark.parametrize(
    ("path", "status", "expected_requests"),
    [
        ("/api/endpoints/3", 404, 1),
        ("/api/stacks/7", 404, 2),
        ("/api/stacks/7", 403, 2),
        ("/api/stacks/7/git/redeploy?endpointId=3", 404, 3),
    ],
)
def test_api_errors_identify_resource_and_stop_the_deployment(
    portainer, path, status, expected_requests, capsys
):
    portainer.replies[path] = (status, {"message": "Resource not found", "details": "private"})
    with pytest.raises(deployment.DeploymentError) as error:
        deployment.redeploy(portainer.environment)
    assert f"HTTP {status}: Resource not found" in str(error.value)
    assert path.removeprefix("/api") in str(error.value)
    assert "private" not in str(error.value)
    assert len(portainer.requests) == expected_requests
    assert "request completed" not in capsys.readouterr().out


@pytest.mark.parametrize("status", [200, 302, 404])
def test_proxy_html_and_redirects_do_not_count_as_api_success(portainer, status):
    portainer.replies["/api/endpoints/3"] = (status, b"<html>proxy page</html>")
    with pytest.raises(deployment.DeploymentError, match="reverse proxy routing"):
        deployment.redeploy(portainer.environment)
    assert len(portainer.requests) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("PORTAINER_URL", ""),
        ("PORTAINER_URL", "https://user:password@portainer.test"),
        ("PORTAINER_URL", "https://portainer.test/#/stacks/7"),
        ("PORTAINER_API_TOKEN", ""),
        ("PORTAINER_STACK_ID", "release-controller"),
        ("PORTAINER_ENDPOINT_ID", "0"),
    ],
)
def test_invalid_secrets_fail_before_any_request(portainer, field, value):
    portainer.environment[field] = value
    with pytest.raises(deployment.DeploymentError):
        deployment.redeploy(portainer.environment)
    assert not portainer.requests


@pytest.mark.parametrize("change", [{"EndpointId": 99}, {"GitConfig": None}])
def test_wrong_environment_and_non_git_stacks_are_not_redeployed(portainer, change):
    portainer.stack.update(change)
    with pytest.raises(deployment.DeploymentError):
        deployment.redeploy(portainer.environment)
    assert [req.get_method() for req in portainer.requests] == ["GET", "GET"]


def test_wrong_git_ref_is_not_redeployed(portainer):
    portainer.stack["GitConfig"]["ReferenceName"] = "refs/heads/main"
    with pytest.raises(deployment.DeploymentError, match="refs/heads/deploy"):
        deployment.redeploy(portainer.environment)
    assert len(portainer.requests) == 2


def test_transport_failure_is_not_retried(portainer):
    portainer.replies["/api/stacks/7/git/redeploy?endpointId=3"] = URLError("connection lost")
    with pytest.raises(deployment.DeploymentOutcomeUnknown, match="before retrying"):
        deployment.redeploy(portainer.environment)
    assert len(portainer.requests) == 3


@pytest.mark.parametrize(
    ("failure", "description"),
    [
        (URLError(socket.gaierror(-2, "private DNS details")), "DNS lookup failed"),
        (URLError(TimeoutError("private timeout details")), "connection timed out"),
        (TimeoutError("private timeout details"), "connection timed out"),
        (
            URLError(ConnectionRefusedError(errno.ECONNREFUSED, "private details")),
            "connection refused",
        ),
        (URLError(OSError(errno.EHOSTUNREACH, "private details")), "network or host unreachable"),
        (URLError(ssl.SSLError("private TLS details")), "TLS handshake or certificate validation"),
        (ConnectionResetError(errno.ECONNRESET, "private details"), "connection reset"),
        (URLError("http://user:password@proxy.test/api-secret"), "transport error"),
    ],
)
def test_get_connection_failures_identify_cause_without_deploying(portainer, failure, description):
    portainer.replies["/api/endpoints/3"] = failure
    with pytest.raises(deployment.DeploymentError) as error:
        deployment.redeploy(portainer.environment)
    assert type(error.value) is deployment.DeploymentError
    assert description in str(error.value)
    assert "timeout=30s" in str(error.value)
    for sensitive in ("private", "password", "api-secret"):
        assert sensitive not in str(error.value)
    assert len(portainer.requests) == 1
    assert portainer.requests[0].get_method() == "GET"


@pytest.mark.parametrize("status", [502, 503, 504])
@pytest.mark.parametrize("body", [b"<html>Gateway timeout</html>", {"message": "Upstream timeout"}])
def test_gateway_failure_does_not_report_or_retry_the_deployment(portainer, status, body, capsys):
    portainer.replies["/api/stacks/7/git/redeploy?endpointId=3"] = (status, body)
    with pytest.raises(deployment.DeploymentOutcomeUnknown) as error:
        deployment.redeploy(portainer.environment)
    assert f"HTTP {status}" in str(error.value)
    assert "may still be deploying" in str(error.value)
    assert "1800s" in str(error.value)
    assert "before retrying" in str(error.value)
    assert "reverse proxy routing" not in str(error.value)
    assert [req.get_method() for req in portainer.requests] == ["GET", "GET", "PUT"]
    assert "request completed" not in capsys.readouterr().out


def test_non_json_redeploy_success_has_unknown_outcome(portainer, capsys):
    portainer.replies["/api/stacks/7/git/redeploy?endpointId=3"] = (200, b"<html>proxy page</html>")
    with pytest.raises(deployment.DeploymentOutcomeUnknown, match="may still be deploying"):
        deployment.redeploy(portainer.environment)
    assert len(portainer.requests) == 3
    assert "request completed" not in capsys.readouterr().out


def test_cli_fails_the_pipeline_for_unknown_deployment_outcome(portainer, monkeypatch, capsys):
    monkeypatch.setattr(deployment.sys, "argv", ["portainer_redeploy"])
    monkeypatch.setattr(deployment.os, "environ", portainer.environment)
    portainer.replies["/api/stacks/7/git/redeploy?endpointId=3"] = (504, b"Gateway timeout")
    with pytest.raises(SystemExit) as error:
        deployment.main()
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "Portainer deployment outcome unknown" in output.err
    assert "HTTP 504" in output.err
    assert "request completed" not in output.out
    assert len(portainer.requests) == 3


def test_cli_returns_failure_without_reporting_success(portainer, monkeypatch, capsys):
    monkeypatch.setattr(deployment.sys, "argv", ["portainer_redeploy"])
    monkeypatch.setattr(deployment.os, "environ", portainer.environment)
    portainer.replies["/api/endpoints/3"] = (404, {"message": "Environment not found"})
    with pytest.raises(SystemExit) as error:
        deployment.main()
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "HTTP 404: Environment not found" in output.err
    assert "request completed" not in output.out


def test_api_error_messages_cannot_reflect_ddns_or_credentials_into_ci(
    portainer, monkeypatch, capsys
):
    hostname = "private-tenant." + "duckdns.org"
    portainer.replies["/api/endpoints/3"] = (
        403,
        {
            "message": f"https://{hostname}/api token=unrelated-synthetic-secret",
            "details": {"env": "private-environment"},
        },
    )
    monkeypatch.setattr(deployment.sys, "argv", ["portainer_redeploy"])
    monkeypatch.setattr(deployment.os, "environ", portainer.environment)
    with pytest.raises(SystemExit) as error:
        deployment.main()
    assert error.value.code == 1
    output = capsys.readouterr()
    for private_value in (hostname, "unrelated-synthetic-secret", "private-environment"):
        assert private_value not in output.out + output.err
    assert "HTTP 403" in output.err
