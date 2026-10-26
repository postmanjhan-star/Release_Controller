"""Execute the exact copyable documentation snippet without real network access."""

import io
import json
import signal
import textwrap
import urllib.request
from pathlib import Path

import pytest


def notification_code():
    doc = Path("docs/ci-integration.md").read_text()
    return textwrap.dedent(doc.split("python - <<'PY'\n", 1)[1].split("      PY", 1)[0])


@pytest.mark.parametrize("outcome", ["accepted", "failure", "redirect", "timeout"])
def test_documented_notification(outcome, monkeypatch, capsys):
    secret = "never-log-this-credential"
    monkeypatch.setenv("RELEASE_CONTROLLER_URL", "https://controller.example")
    monkeypatch.setenv("RELEASE_CONTROLLER_CI_TOKEN", secret)
    monkeypatch.setenv("DRONE_BUILD_NUMBER", "123")
    monkeypatch.setattr(signal, "signal", lambda *args: None)
    monkeypatch.setattr(signal, "alarm", lambda *args: None)

    class Response(io.BytesIO):
        status = {"accepted": 202, "redirect": 302}.get(outcome, 500)

    class Opener:
        def open(self, request, timeout):
            assert timeout == 10
            assert request.full_url == "https://controller.example/api/v1/ci/deployments"
            assert request.get_header("Authorization") == "Bearer " + secret
            assert json.loads(request.data) == dict(
                project_key="wordpress-mcp",
                component_key="server",
                source_build_number=123,
                target="production",
            )
            if outcome == "timeout":
                raise TimeoutError(secret)
            return Response(
                json.dumps({"request_id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"}).encode()
            )

    def opener(handler):
        assert handler.redirect_request(None, None, 302, "", {}, "https://other.example") is None
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", opener)
    code = compile(notification_code(), "documented-drone-step", "exec")
    if outcome == "accepted":
        exec(code, {})
    else:
        with pytest.raises(SystemExit) as exc:
            exec(code, {})
        assert exc.value.code == 1
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    assert ("accepted deployment request" in output.out) == (outcome == "accepted")
