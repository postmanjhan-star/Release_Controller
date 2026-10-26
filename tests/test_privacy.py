"""Privacy regressions use synthetic values and never read private deployment settings."""

import io
import logging
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import PrivacyFormatter, configure_logging
from app.core.redaction import DDNS_HOST, redact, redact_log
from app.db.models.scheduling import EmailOutbox
from app.domain.shared.time import utc_now
from app.services.notification_service import NotificationService


def test_public_documentation_and_examples_have_no_ddns():
    root = Path(__file__).resolve().parents[1]
    paths = [*root.glob("*.md"), *root.glob("*.yml"), root / ".env.example", root / ".drone.yml"]
    paths.extend((root / "docs").rglob("*.md"))
    locations = []
    for path in paths:
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if DDNS_HOST.search(line):
                # A failing regression must never print the matched domain.
                locations.append(f"{path.relative_to(root)}:{number}")
    assert not locations, f"Private DDNS found at {locations}"


@pytest.mark.parametrize("suffix", ["synology.me", "duckdns.org", "ddns.net", "myqnapcloud.com"])
def test_shared_redaction_handles_bare_ddns_and_nested_values(suffix):
    host = "private-tenant." + suffix
    result = redact({"details": [host, "https://" + host + "/api"]})
    assert host not in repr(result)
    assert "[REDACTED_HOST]" in repr(result)


@pytest.mark.parametrize(
    "value",
    [
        "Authorization: Basic synthetic-header-secret",
        "{'Authorization': 'token synthetic-header-secret'}",
        "Cookie: session=synthetic-header-secret; other=value",
        "Set-Cookie: session=synthetic-header-secret; HttpOnly",
        "X-API-Key: synthetic-header-secret",
        "headers=[(b'authorization', b'Basic synthetic-header-secret')]",
        "GET /api/v1/auth/callback?code=synthetic-header-secret&state=synthetic-header-secret",
    ],
)
def test_log_redaction_masks_headers_and_login_codes(value):
    assert "synthetic-header-secret" not in redact_log(value)


def test_formatter_redacts_interpolated_values_and_chained_tracebacks():
    host = "private-tenant." + "duckdns.org"
    configured_secret = "unlabelled-synthetic-credential"
    try:
        try:
            raise ValueError(f"https://{host}/api token=synthetic-exception-secret")
        except ValueError as exc:
            raise RuntimeError(configured_secret) from exc
    except RuntimeError:
        record = logging.LogRecord(
            "privacy",
            logging.ERROR,
            __file__,
            1,
            "Request failed for %s; Authorization: Basic synthetic-header-secret",
            (host,),
            sys.exc_info(),
        )
    rendered = PrivacyFormatter(secrets=(configured_secret,)).format(record)
    for private_value in (
        host,
        configured_secret,
        "synthetic-exception-secret",
        "synthetic-header-secret",
    ):
        assert private_value not in rendered
    assert "RuntimeError" in rendered and "ValueError" in rendered
    assert "Request failed" in rendered and "[REDACTED_URL]" in rendered


def test_logging_wraps_server_handlers_and_keeps_stdio_protocol_clean():
    root = logging.getLogger()
    server = logging.getLogger("uvicorn.access")
    before = (
        root.handlers[:],
        root.level,
        server.handlers[:],
        server.propagate,
        server.level,
        server.disabled,
    )
    output, access = io.StringIO(), io.StringIO()
    try:
        server.handlers = [logging.StreamHandler(access)]
        server.propagate, server.level, server.disabled = False, logging.INFO, False
        configure_logging("INFO", stream=output, secrets=("synthetic-configured-secret",))
        root.warning("HTTP Request %s", "https://private.internal.example/api")
        server.warning("HTTP access token=%s", "synthetic-configured-secret")
        assert "private.internal.example" not in output.getvalue()
        assert "synthetic-configured-secret" not in access.getvalue()
        assert "HTTP Request" in output.getvalue() and "HTTP access" in access.getvalue()
    finally:
        for handler in root.handlers:
            handler.close()
        (
            root.handlers,
            root.level,
            server.handlers,
            server.propagate,
            server.level,
            server.disabled,
        ) = before


def test_smtp_error_history_never_persists_raw_exception(client):
    settings = get_settings().model_copy(update={"smtp_host": "smtp.test"})

    class FailingSender:
        def send(self, outbox):
            raise RuntimeError("https://private.internal.example password=synthetic-smtp-secret")

    with client.session_factory() as db:
        row = EmailOutbox(
            recipients="recipient@example.com",
            subject="test",
            body="test",
            next_attempt_at=utc_now() - timedelta(seconds=1),
        )
        db.add(row)
        db.commit()
        service = NotificationService(db, settings, sender=FailingSender())
        assert service.deliver_pending() == 0
        row = db.scalar(select(EmailOutbox))
        assert row.status == "FAILED" and row.attempts == 1
        assert "synthetic-smtp-secret" not in row.last_error
        assert "private.internal.example" not in row.last_error
        assert "SMTP" in row.last_error


def test_schedule_failure_response_does_not_persist_raw_exception(client, monkeypatch):
    from app.services.release_orchestrator import ReleaseOrchestrator

    response = client.post(
        "/api/v1/schedules",
        json={
            "mode": "FRONTEND_ONLY",
            "frontend_build_number": 123,
            "target": "staging",
            "scheduled_for": (utc_now() - timedelta(minutes=1)).isoformat(),
            "timezone": "UTC",
        },
    )
    assert response.status_code == 201

    def fail(*args, **kwargs):
        raise RuntimeError("https://private.internal.example token=synthetic-schedule-secret")

    monkeypatch.setattr(ReleaseOrchestrator, "promote_standalone", fail)
    assert client.post("/api/v1/schedules/run-due").status_code == 200
    schedule = client.get("/api/v1/schedules/" + response.json()["id"]).json()
    assert schedule["status"] == "FAILED"
    assert "synthetic-schedule-secret" not in schedule["error_message"]
    assert "private.internal.example" not in schedule["error_message"]
    assert "deployment history" in schedule["error_message"]
