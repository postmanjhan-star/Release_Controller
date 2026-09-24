import json
import ssl
from datetime import datetime, timedelta, timezone
from email import policy
from email.header import Header
from email.parser import BytesParser

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.db.models.scheduling import EmailOutbox
from app.db.session import get_db
from app.infrastructure.di.injection import get_drone_client, get_drone_clients
from app.integrations.drone.schemas import DroneBuild
from app.services.drone_build_service import DroneBuildService
from app.services.notification_service import SmtpSender
from app.services.schedule_service import ScheduleRunner
from app.services.upstream_clients import FixedDroneClients


class ScheduleDroneClient:
    def __init__(self) -> None:
        self.promotions: list[tuple[str, str, int, str]] = []

    def get_build(self, owner: str, repo: str, build_number: int) -> DroneBuild:
        if build_number == 123:
            return DroneBuild(
                number=123,
                status="success",
                event="push",
                branch="main",
                commit_sha="a" * 40,
            )
        return DroneBuild(
            number=124,
            status="success",
            event="promote",
            target="pre-production",
            branch="main",
            commit_sha="a" * 40,
        )

    def promote_build(self, owner: str, repo: str, build_number: int, target: str) -> DroneBuild:
        self.promotions.append((owner, repo, build_number, target))
        return DroneBuild(
            number=124,
            status="pending",
            event="promote",
            target=target,
            branch="main",
            commit_sha="a" * 40,
        )


class FakeSmtpConnection:
    def __init__(self, **options) -> None:
        self.options = options
        self.starttls_context = None

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def starttls(self, *, context) -> None:
        self.starttls_context = context

    def login(self, _username: str, _password: str) -> None:
        return None

    def send_message(self, _message) -> None:
        return None


def test_due_schedule_reuses_orchestrator_and_can_be_listed(client: TestClient) -> None:
    drone = ScheduleDroneClient()
    client.app.dependency_overrides[get_drone_client] = lambda: drone
    client.app.dependency_overrides[get_drone_clients] = lambda: FixedDroneClients(drone)
    payload = {
        "mode": "FRONTEND_ONLY",
        "frontend_build_number": 123,
        "target": "pre-production",
        "scheduled_for": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "timezone": "Asia/Taipei",
        "requested_by": "release-operator",
        "notification_recipients": ["schedule-owner@example.com"],
    }
    created = client.post("/api/v1/schedules", json=payload)
    assert created.status_code == 201
    assert created.json()["status"] == "PENDING"
    assert created.json()["timezone"] == "Asia/Taipei"

    run = client.post("/api/v1/schedules/run-due")
    assert run.status_code == 200
    assert run.json() == {"processed": 1}
    schedule = client.get(f"/api/v1/schedules/{created.json()['id']}").json()
    assert schedule["status"] == "SUCCEEDED"
    assert schedule["deployment_id"] is not None
    assert drone.promotions == [("102573", "SMT-Assistant", 123, "pre-production")]
    db_generator = client.app.dependency_overrides[get_db]()
    db = next(db_generator)
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    db_generator.close()
    settings = get_settings()
    runner = ScheduleRunner(factory, lambda _db: DroneBuildService(drone, settings), settings)
    assert runner.refresh_scheduled_deployments() == 1
    assert (
        client.get(f"/api/v1/deployments/{schedule['deployment_id']}").json()["status"] == "SUCCESS"
    )
    assert client.post("/api/v1/notifications/dispatch").json() == {"queued": 1, "sent": 0}
    outbox = client.get("/api/v1/notifications/outbox").json()["items"]
    assert outbox[0]["recipients"] == ["schedule-owner@example.com"]


def test_schedule_validation_and_cancel(client: TestClient) -> None:
    invalid = client.post(
        "/api/v1/schedules",
        json={
            "mode": "BUNDLE",
            "frontend_build_number": 123,
            "scheduled_for": "2030-01-01T09:00:00",
            "timezone": "UTC",
        },
    )
    assert invalid.status_code == 422

    missing_release_details = client.post(
        "/api/v1/schedules",
        json={
            "mode": "FRONTEND_ONLY",
            "frontend_build_number": 123,
            "target": "production",
            "scheduled_for": "2030-01-01T09:00:00",
            "timezone": "UTC",
        },
    )
    assert missing_release_details.status_code == 422

    created = client.post(
        "/api/v1/schedules",
        json={
            "mode": "BACKEND_ONLY",
            "backend_build_number": 456,
            "scheduled_for": "2030-01-01T09:00:00",
            "timezone": "UTC",
        },
    )
    cancelled = client.delete(f"/api/v1/schedules/{created.json()['id']}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert client.delete(f"/api/v1/schedules/{created.json()['id']}").status_code == 409


def test_notification_recipients_can_be_managed(client: TestClient) -> None:
    created = client.post(
        "/api/v1/notifications/recipients",
        json={"email": "  Release.Owner@Example.COM  "},
    )
    assert created.status_code == 201
    assert created.json()["email"] == "release.owner@example.com"

    duplicate = client.post(
        "/api/v1/notifications/recipients",
        json={"email": "release.owner@example.com"},
    )
    assert duplicate.status_code == 409
    assert (
        client.post("/api/v1/notifications/recipients", json={"email": "not-an-email"}).status_code
        == 422
    )

    recipients = client.get("/api/v1/notifications/recipients")
    assert recipients.json()["total"] == 1
    assert recipients.json()["items"][0]["email"] == "release.owner@example.com"

    deleted = client.delete(f"/api/v1/notifications/recipients/{created.json()['id']}")
    assert deleted.status_code == 204
    assert client.get("/api/v1/notifications/recipients").json()["total"] == 0
    assert (
        client.delete(f"/api/v1/notifications/recipients/{created.json()['id']}").status_code == 404
    )


def test_production_schedule_notifies_selected_managed_recipients(
    client: TestClient,
) -> None:
    drone = ScheduleDroneClient()
    client.app.dependency_overrides[get_drone_client] = lambda: drone
    client.app.dependency_overrides[get_drone_clients] = lambda: FixedDroneClients(drone)
    for email in ["first@example.com", "second@example.com"]:
        assert (
            client.post("/api/v1/notifications/recipients", json={"email": email}).status_code
            == 201
        )

    created = client.post(
        "/api/v1/schedules",
        json={
            "mode": "FRONTEND_ONLY",
            "frontend_build_number": 123,
            "target": "production",
            "scheduled_for": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "timezone": "Asia/Taipei",
            "notification_recipients": ["release-owner@example.com", "first@example.com"],
            "release_version": "v1.5.0",
            "release_notes": "修正登入逾時\n提升頁面載入速度",
        },
    )
    assert created.status_code == 201
    scheduled_notice = client.get("/api/v1/notifications/outbox").json()
    assert scheduled_notice["total"] == 1
    assert scheduled_notice["items"][0]["schedule_id"] == created.json()["id"]
    assert scheduled_notice["items"][0]["recipients"] == [
        "release-owner@example.com",
        "first@example.com",
    ]
    assert scheduled_notice["items"][0]["subject"] == "【SMT Assistant】預計更新通知（v1.5.0）"
    schedule = client.get(f"/api/v1/schedules/{created.json()['id']}").json()
    assert schedule["notification_status"] == "PENDING"
    assert schedule["notification_sent_at"] is None
    assert client.post("/api/v1/schedules/run-due").json() == {"processed": 1}

    db_generator = client.app.dependency_overrides[get_db]()
    db = next(db_generator)
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    db_generator.close()
    settings = get_settings()
    runner = ScheduleRunner(factory, lambda _db: DroneBuildService(drone, settings), settings)
    assert runner.refresh_scheduled_deployments() == 1

    assert client.post("/api/v1/notifications/dispatch").json() == {"queued": 1, "sent": 0}
    outbox = client.get("/api/v1/notifications/outbox").json()["items"]
    terminal_notice = next(item for item in outbox if item["workflow_event_id"] is not None)
    assert terminal_notice["recipients"] == [
        "release-owner@example.com",
        "first@example.com",
    ]


def test_schedule_notice_uses_chinese_template_and_local_time(client: TestClient) -> None:
    created = client.post(
        "/api/v1/schedules",
        json={
            "mode": "FRONTEND_ONLY",
            "frontend_build_number": 123,
            "target": "production",
            "scheduled_for": "2026-08-20T18:30:00",
            "timezone": "Asia/Taipei",
            "notification_recipients": ["release-owner@example.com"],
            "release_version": "v1.5.0",
            "release_notes": "新增批次操作\n- 修正排程顯示",
        },
    )

    assert created.status_code == 201
    assert created.json()["release_version"] == "v1.5.0"
    assert created.json()["release_notes"] == "新增批次操作\n- 修正排程顯示"

    db_generator = client.app.dependency_overrides[get_db]()
    db = next(db_generator)
    notice = db.scalar(select(EmailOutbox))
    db_generator.close()

    assert notice.subject == "【SMT Assistant】預計更新通知（v1.5.0）"
    assert notice.body == "\n".join(
        [
            "SMT Assistant 預計更新",
            "",
            "時間：",
            "2026/08/20 18:30",
            "",
            "影響：",
            "預計 30 ~ 60 分鐘暫停服務",
            "",
            "版本：",
            "v1.5.0",
            "",
            "更新內容：",
            "- 新增批次操作",
            "- 修正排程顯示",
        ]
    )


def test_terminal_workflow_event_is_sent_once_via_outbox(client: TestClient, monkeypatch) -> None:
    drone = ScheduleDroneClient()
    settings = Settings(
        database_url="sqlite://",
        smtp_host="smtp.test",
        smtp_from_address="release-controller@example.com",
        smtp_default_recipients="release-team@example.com",
    )
    client.app.dependency_overrides[get_settings] = lambda: settings
    client.app.dependency_overrides[get_drone_client] = lambda: drone
    client.app.dependency_overrides[get_drone_clients] = lambda: FixedDroneClients(drone)
    sent: list[str] = []
    monkeypatch.setattr(SmtpSender, "send", lambda _self, outbox: sent.append(outbox.subject))

    deployment = client.post(
        "/api/v1/projects/default/components/frontend/builds/123/promote"
    ).json()
    refreshed = client.post(f"/api/v1/deployments/{deployment['id']}/refresh")
    assert refreshed.json()["status"] == "SUCCESS"

    first = client.post("/api/v1/notifications/dispatch")
    second = client.post("/api/v1/notifications/dispatch")
    assert first.json() == {"queued": 1, "sent": 1}
    assert second.json() == {"queued": 0, "sent": 0}
    assert len(sent) == 1
    outbox = client.get("/api/v1/notifications/outbox").json()
    assert outbox["total"] == 1
    assert outbox["items"][0]["status"] == "SENT"
    assert outbox["items"][0]["recipients"] == ["release-team@example.com"]


def test_smtp_starttls_verifies_certificates_by_default(monkeypatch) -> None:
    connections: list[FakeSmtpConnection] = []

    def create_connection(**options):
        connection = FakeSmtpConnection(**options)
        connections.append(connection)
        return connection

    monkeypatch.setattr("app.services.notification_service.smtplib.SMTP", create_connection)
    settings = Settings(
        smtp_host="mail.example.test",
        smtp_starttls=True,
        smtp_tls_verify=True,
    )
    outbox = type(
        "Outbox",
        (),
        {
            "recipients": "notes-user@example.com",
            "subject": "Release completed",
            "body": "SUCCESS",
        },
    )()

    SmtpSender(settings).send(outbox)

    assert connections[0].options["host"] == "mail.example.test"
    assert connections[0].starttls_context.verify_mode == ssl.CERT_REQUIRED
    assert connections[0].starttls_context.check_hostname is True


def test_smtp_keeps_office_mime_type_valid_with_chinese_filename(monkeypatch) -> None:
    messages = []

    class CaptureSmtp(FakeSmtpConnection):
        def send_message(self, message) -> None:
            messages.append(message)

    monkeypatch.setattr("app.services.notification_service.smtplib.SMTP", CaptureSmtp)
    outbox = type(
        "Outbox",
        (),
        {
            "recipients": "release-owner@example.com",
            "subject": "Release notice",
            "body": "Scheduled update",
            "attachment_filename": "20260820產線會議記錄.xlsx",
            "attachment_content_type": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            "attachment_data": b"PK-xlsx",
        },
    )()

    SmtpSender(Settings(smtp_host="mail.example.test", smtp_starttls=False)).send(outbox)

    raw_message = messages[0].as_bytes()
    assert (
        b"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;"
        in raw_message
    )
    assert b"Content-Type: application/=?utf-8?" not in raw_message
    attachment = list(messages[0].iter_attachments())[0]
    assert attachment.get_filename() == "20260820產線會議記錄.xlsx"
    assert attachment.get_param("name", header="Content-Type") == "20260820產線會議記錄.xlsx"


def test_smtp_never_splits_filename_into_rfc2231_continuations(monkeypatch) -> None:
    # Outlook and Exchange do not implement RFC 2231 parameter continuations
    # (filename*0*, filename*1*, ...). When they cannot read a filename they
    # show the attachment as ATT00001.dat, so the wire format must never use
    # them, no matter how long the Chinese filename is.
    long_names = [
        "1月29日SMT上料比對程式說明記錄.pptx",
        "2026年度第三季生產線設備維護與更新計畫說明文件最終版本含附錄.docx",
        ("超長" * 120) + ".xlsx",
    ]
    for filename in long_names:
        message = _capture_attachment_message(monkeypatch, filename, "application/octet-stream")
        raw_message = message.as_bytes()
        assert b"filename*0*" not in raw_message
        assert b"name*0*" not in raw_message
        assert max(len(line) for line in raw_message.split(b"\r\n")) <= 998


def test_smtp_sends_outlook_readable_filename_for_chinese_attachment(monkeypatch) -> None:
    filename = "1月29日SMT上料比對程式說明記錄.pptx"
    content_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    message = _capture_attachment_message(monkeypatch, filename, content_type)
    raw_message = message.as_bytes()

    # Legacy Outlook/Exchange parsers only understand the RFC 2047 encoded word.
    encoded_word = Header(filename, "utf-8").encode(maxlinelen=10_000).encode()
    assert b'filename="' + encoded_word + b'"' in raw_message
    assert b'name="' + encoded_word + b'"' in raw_message
    # RFC-compliant clients read the extended parameter and must still win.
    parsed = BytesParser(policy=policy.default).parsebytes(raw_message)
    attachment = list(parsed.iter_attachments())[0]
    assert attachment.get_filename() == filename
    assert attachment.get_content_type() == content_type


def _capture_attachment_message(monkeypatch, filename: str, content_type: str):
    messages = []

    class CaptureSmtp(FakeSmtpConnection):
        def send_message(self, message) -> None:
            messages.append(message)

    monkeypatch.setattr("app.services.notification_service.smtplib.SMTP", CaptureSmtp)
    outbox = type(
        "Outbox",
        (),
        {
            "recipients": "release-owner@example.com",
            "subject": "Release notice",
            "body": "Scheduled update",
            "attachment_filename": filename,
            "attachment_content_type": content_type,
            "attachment_data": b"PK-payload",
        },
    )()
    SmtpSender(Settings(smtp_host="mail.example.test", smtp_starttls=False)).send(outbox)
    return messages[0]


def test_schedule_attachment_is_persisted_and_added_to_email(
    client: TestClient, monkeypatch
) -> None:
    payload = {
        "mode": "FRONTEND_ONLY",
        "frontend_build_number": 123,
        "target": "production",
        "scheduled_for": "2030-01-01T09:00:00",
        "timezone": "Asia/Taipei",
        "notification_recipients": ["release-owner@example.com"],
        "release_version": "v2.0.0",
        "release_notes": "Attachment test",
    }
    created = client.post(
        "/api/v1/schedules",
        data={"payload": json.dumps(payload)},
        files={"attachment": ("change-list.pdf", b"%PDF-test", "application/pdf")},
    )

    assert created.status_code == 201
    assert created.json()["attachment_filename"] == "change-list.pdf"
    assert created.json()["attachment_content_type"] == "application/pdf"
    assert created.json()["attachment_size"] == 9

    db_generator = client.app.dependency_overrides[get_db]()
    db = next(db_generator)
    outbox = db.scalar(select(EmailOutbox))
    assert outbox.attachment_data == b"%PDF-test"
    db_generator.close()

    messages = []

    class CaptureSmtp(FakeSmtpConnection):
        def send_message(self, message) -> None:
            messages.append(message)

    monkeypatch.setattr("app.services.notification_service.smtplib.SMTP", CaptureSmtp)
    SmtpSender(Settings(smtp_host="mail.example.test", smtp_starttls=False)).send(outbox)
    attachments = list(messages[0].iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "change-list.pdf"
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_param("name", header="Content-Type") == "change-list.pdf"
    assert attachments[0].get_payload(decode=True) == b"%PDF-test"


def test_excel_attachment_type_is_inferred_for_outlook_compatibility(
    client: TestClient,
) -> None:
    payload = {
        "mode": "FRONTEND_ONLY",
        "frontend_build_number": 123,
        "target": "production",
        "scheduled_for": "2030-01-01T09:00:00",
        "timezone": "Asia/Taipei",
        "notification_recipients": ["release-owner@example.com"],
        "release_version": "v2.0.0",
        "release_notes": "Excel attachment test",
    }
    created = client.post(
        "/api/v1/schedules",
        data={"payload": json.dumps(payload)},
        files={
            "attachment": (
                "release-plan.xlsx",
                b"PK-xlsx",
                "application/octet-stream",
            )
        },
    )

    assert created.status_code == 201
    assert created.json()["attachment_filename"] == "release-plan.xlsx"
    assert (
        created.json()["attachment_content_type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_powerpoint_attachment_type_is_inferred_for_outlook_compatibility(
    client: TestClient,
) -> None:
    payload = {
        "mode": "FRONTEND_ONLY",
        "frontend_build_number": 123,
        "target": "production",
        "scheduled_for": "2030-01-01T09:00:00",
        "timezone": "Asia/Taipei",
        "notification_recipients": ["release-owner@example.com"],
        "release_version": "v2.0.0",
        "release_notes": "PowerPoint attachment test",
    }
    created = client.post(
        "/api/v1/schedules",
        data={"payload": json.dumps(payload)},
        files={
            "attachment": (
                "1月29日SMT上料比對程式說明記錄.pptx",
                b"PK-pptx",
                "application/octet-stream",
            )
        },
    )

    assert created.status_code == 201
    assert created.json()["attachment_filename"] == "1月29日SMT上料比對程式說明記錄.pptx"
    assert (
        created.json()["attachment_content_type"]
        == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )


def test_word_attachment_type_is_inferred_for_outlook_compatibility(
    client: TestClient,
) -> None:
    payload = {
        "mode": "FRONTEND_ONLY",
        "frontend_build_number": 123,
        "target": "production",
        "scheduled_for": "2030-01-01T09:00:00",
        "timezone": "Asia/Taipei",
        "notification_recipients": ["release-owner@example.com"],
        "release_version": "v2.0.0",
        "release_notes": "Word attachment test",
    }
    created = client.post(
        "/api/v1/schedules",
        data={"payload": json.dumps(payload)},
        files={
            "attachment": (
                "交接寶典.docx",
                b"PK-docx",
                "application/octet-stream",
            )
        },
    )

    assert created.status_code == 201
    assert created.json()["attachment_filename"] == "交接寶典.docx"
    assert (
        created.json()["attachment_content_type"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_promote_attachment_follows_terminal_notification(client: TestClient) -> None:
    drone = ScheduleDroneClient()
    settings = Settings(
        smtp_default_recipients="release-team@example.com",
        notification_attachment_max_bytes=1024,
    )
    client.app.dependency_overrides[get_settings] = lambda: settings
    client.app.dependency_overrides[get_drone_client] = lambda: drone
    client.app.dependency_overrides[get_drone_clients] = lambda: FixedDroneClients(drone)

    promoted = client.post(
        "/api/v1/projects/default/components/frontend/builds/123/promote",
        data={"payload": json.dumps({"target": "production"})},
        files={"attachment": ("approval.txt", b"approved", "text/plain")},
    )
    assert promoted.status_code == 201
    assert promoted.json()["attachment_filename"] == "approval.txt"

    refreshed = client.post(f"/api/v1/deployments/{promoted.json()['id']}/refresh")
    assert refreshed.json()["status"] == "SUCCESS"
    assert client.post("/api/v1/notifications/dispatch").json() == {"queued": 1, "sent": 0}
    outbox = client.get("/api/v1/notifications/outbox").json()["items"][0]
    assert outbox["attachment_filename"] == "approval.txt"
    assert outbox["attachment_content_type"] == "text/plain"
    assert outbox["attachment_size"] == 8


def test_bundle_promote_accepts_attachment(client: TestClient) -> None:
    drone = ScheduleDroneClient()
    client.app.dependency_overrides[get_drone_client] = lambda: drone
    client.app.dependency_overrides[get_drone_clients] = lambda: FixedDroneClients(drone)
    response = client.post(
        "/api/v1/releases/promote",
        data={
            "payload": json.dumps(
                {
                    "frontend_build_number": 123,
                    "backend_build_number": 123,
                    "target": "production",
                }
            )
        },
        files={"attachment": ("release-plan.docx", b"docx", "application/octet-stream")},
    )

    assert response.status_code == 201
    assert response.json()["attachment_filename"] == "release-plan.docx"
    assert response.json()["attachment_size"] == 4


def test_attachment_size_limit_is_enforced(client: TestClient) -> None:
    settings = Settings(notification_attachment_max_bytes=4)
    client.app.dependency_overrides[get_settings] = lambda: settings
    response = client.post(
        "/api/v1/projects/default/components/frontend/builds/123/promote",
        data={"payload": "{}"},
        files={"attachment": ("too-large.txt", b"12345", "text/plain")},
    )
    assert response.status_code == 413
