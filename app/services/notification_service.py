import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email import policy
from email.header import Header
from email.message import EmailMessage
from urllib.parse import quote
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.orchestration import Deployment, ReleaseBundle, WorkflowEvent
from app.db.models.scheduling import (
    DeploymentSchedule,
    EmailNotificationCursor,
    EmailOutbox,
    NotificationRecipient,
)
from app.domain.orchestration.value_objects import WorkflowEventType
from app.domain.scheduling.value_objects import EmailDeliveryStatus
from app.domain.shared.time import utc_now
from app.services.attachment_service import attachment_values

NOTIFIABLE_EVENT_TYPES = {
    WorkflowEventType.STAGE_FAILED.value,
    WorkflowEventType.DEPLOYMENT_CANCELLED.value,
    WorkflowEventType.RELEASE_COMPLETED.value,
}


def _split_recipients(value: str | None) -> list[str]:
    if not value:
        return []
    return list(
        dict.fromkeys(item.strip() for item in value.replace(";", ",").split(",") if item.strip())
    )


def _merge_recipients(*groups: list[str]) -> list[str]:
    return list(dict.fromkeys(address.lower() for group in groups for address in group))


def _format_schedule_time(schedule: DeploymentSchedule) -> str:
    scheduled_for = schedule.scheduled_for_utc
    if scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
    return scheduled_for.astimezone(ZoneInfo(schedule.timezone)).strftime("%Y/%m/%d %H:%M")


def _format_release_notes(value: str | None) -> list[str]:
    notes = [line.strip().lstrip("-*• ").strip() for line in (value or "").splitlines()]
    return [f"- {note}" for note in notes if note] or ["- 未提供"]


# Keeps every generated header line well inside the 998-octet SMTP limit once
# the parameter name and the folding space are added.
MAX_FILENAME_PARAM_OCTETS = 850


def _encoded_word(filename: str) -> str:
    """RFC 2047 encoding of a filename, always as a single encoded word."""
    return Header(filename, "utf-8").encode(maxlinelen=MAX_FILENAME_PARAM_OCTETS * 2)


def _extended_parameter(filename: str) -> str:
    """RFC 2231 extended value of a filename, never split into continuations."""
    return "utf-8''" + quote(filename, safe="")


def _fits_one_line(filename: str) -> bool:
    return (
        max(len(_encoded_word(filename)), len(_extended_parameter(filename)))
        <= MAX_FILENAME_PARAM_OCTETS
    )


def _shorten_filename(filename: str) -> str:
    """Trim the stem until both encodings fit on a single unfolded header line.

    Only reachable for filenames far longer than anything a person types; a
    shortened name still opens, while a wrapped one arrives as ATT00001.dat.
    """
    stem, dot, extension = filename.rpartition(".")
    if not dot:
        stem, extension = filename, ""
    candidate = filename
    while stem and not _fits_one_line(candidate):
        stem = stem[:-1]
        candidate = f"{stem}{dot}{extension}"
    return candidate


def _filename_header(prefix: str, parameter: str, filename: str) -> str:
    """Build a Content-Type/Content-Disposition value carrying the filename.

    Python's header folder encodes a non-ASCII parameter as RFC 2231 and splits
    it into continuations (``filename*0*``, ``filename*1*``, ...) as soon as it
    outgrows one line. Outlook and Exchange do not implement continuations, so
    they lose the name and the extension and fall back to showing the part as
    ATT00001.dat. Emitting the value ourselves keeps it on one line and pairs
    the RFC 2047 encoded word those clients do understand with the RFC 2231
    parameter standards-compliant clients prefer.
    """
    if filename.isascii():
        escaped = filename.replace("\\", "\\\\").replace('"', '\\"')
        return f'{prefix}; {parameter}="{escaped}"'
    display = filename if _fits_one_line(filename) else _shorten_filename(filename)
    return (
        f'{prefix};\n {parameter}="{_encoded_word(display)}";'
        f"\n {parameter}*={_extended_parameter(display)}"
    )


class DuplicateNotificationRecipientError(Exception):
    pass


class NotificationRecipientNotFoundError(Exception):
    pass


class NotificationRecipientService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, email: str) -> NotificationRecipient:
        recipient = NotificationRecipient(email=email)
        self.db.add(recipient)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise DuplicateNotificationRecipientError from exc
        self.db.refresh(recipient)
        return recipient

    def list(self) -> list[NotificationRecipient]:
        return list(
            self.db.scalars(
                select(NotificationRecipient).order_by(
                    NotificationRecipient.email.asc(), NotificationRecipient.id.asc()
                )
            ).all()
        )

    def delete(self, recipient_id: str) -> None:
        recipient = self.db.get(NotificationRecipient, recipient_id)
        if recipient is None:
            raise NotificationRecipientNotFoundError
        self.db.delete(recipient)
        self.db.commit()


class SmtpSender:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, outbox: EmailOutbox) -> None:
        if not self.settings.smtp_host:
            raise RuntimeError("SMTP_HOST is not configured")
        # refold_source="none" keeps the attachment headers written by
        # _filename_header() exactly as built; every other header is a parsed
        # header object and is still folded by the policy as usual.
        message = EmailMessage(policy=policy.SMTP.clone(refold_source="none"))
        message["From"] = self.settings.smtp_from_address
        message["To"] = ", ".join(_split_recipients(outbox.recipients))
        message["Subject"] = outbox.subject
        message.set_content(outbox.body)
        attachment_data = getattr(outbox, "attachment_data", None)
        attachment_filename = getattr(outbox, "attachment_filename", None)
        if attachment_data and attachment_filename:
            content_type = (
                getattr(outbox, "attachment_content_type", None) or "application/octet-stream"
            )
            maintype, subtype = content_type.split("/", 1)
            message.add_attachment(attachment_data, maintype=maintype, subtype=subtype)
            attachment_part = list(message.iter_attachments())[-1]
            # Content-Disposition/filename is the standard. Some Exchange and
            # Outlook paths still rely on the legacy Content-Type/name parameter;
            # without it they can expose a valid attachment as att*.dat.
            del attachment_part["Content-Type"]
            del attachment_part["Content-Disposition"]
            attachment_part.set_raw(
                "Content-Type", _filename_header(content_type, "name", attachment_filename)
            )
            attachment_part.set_raw(
                "Content-Disposition",
                _filename_header("attachment", "filename", attachment_filename),
            )
        tls_context = (
            ssl.create_default_context(cafile=self.settings.smtp_tls_ca_file)
            if self.settings.smtp_tls_verify
            else ssl._create_unverified_context()
        )
        smtp_class = smtplib.SMTP_SSL if self.settings.smtp_ssl else smtplib.SMTP
        connection_options = {
            "host": self.settings.smtp_host,
            "port": self.settings.smtp_port,
            "timeout": self.settings.smtp_timeout_seconds,
        }
        if self.settings.smtp_ssl:
            connection_options["context"] = tls_context
        with smtp_class(**connection_options) as client:
            if self.settings.smtp_starttls and not self.settings.smtp_ssl:
                client.starttls(context=tls_context)
            if self.settings.smtp_username:
                client.login(self.settings.smtp_username, self.settings.smtp_password or "")
            client.send_message(message)


class NotificationService:
    def __init__(self, db: Session, settings: Settings, sender: SmtpSender | None = None) -> None:
        self.db = db
        self.settings = settings
        self.sender = sender or SmtpSender(settings)

    def enqueue_schedule_created(self, schedule: DeploymentSchedule) -> int:
        managed_targets = {
            target.casefold()
            for target in _split_recipients(self.settings.smtp_managed_recipient_targets)
        }
        if schedule.target.casefold() not in managed_targets:
            return 0
        recipients = _split_recipients(schedule.notification_recipients)
        if not recipients:
            recipients = _split_recipients(self.settings.smtp_default_recipients)
        if not recipients:
            return 0
        self.db.add(
            EmailOutbox(
                schedule_id=schedule.id,
                recipients=",".join(recipients),
                subject=(
                    f"【SMT Assistant】預計更新通知（{schedule.release_version}）"
                    if schedule.release_version
                    else "【SMT Assistant】預計更新通知"
                ),
                body="\n".join(
                    [
                        "SMT Assistant 預計更新",
                        "",
                        "時間：",
                        _format_schedule_time(schedule),
                        "",
                        "影響：",
                        "預計 30 ~ 60 分鐘暫停服務",
                        "",
                        "版本：",
                        schedule.release_version or "未提供",
                        "",
                        "更新內容：",
                        *_format_release_notes(schedule.release_notes),
                    ]
                ),
                **attachment_values(schedule),
            )
        )
        return 1

    def enqueue_new_events(self) -> int:
        cursor = self.db.get(EmailNotificationCursor, "workflow_events")
        if cursor is None:
            cursor = EmailNotificationCursor(
                id="workflow_events",
                after_created_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
                after_event_id="",
            )
            self.db.add(cursor)
            self.db.flush()
        existing = select(EmailOutbox.workflow_event_id).where(
            EmailOutbox.workflow_event_id.is_not(None)
        )
        events = list(
            self.db.scalars(
                select(WorkflowEvent)
                .where(
                    WorkflowEvent.event_type.in_(NOTIFIABLE_EVENT_TYPES),
                    WorkflowEvent.id.not_in(existing),
                    or_(
                        WorkflowEvent.created_at > cursor.after_created_at,
                        (WorkflowEvent.created_at == cursor.after_created_at)
                        & (WorkflowEvent.id > cursor.after_event_id),
                    ),
                )
                .order_by(WorkflowEvent.created_at.asc())
            ).all()
        )
        queued = 0
        default_recipients = _split_recipients(self.settings.smtp_default_recipients)
        for event in events:
            links = []
            if event.release_bundle_id is not None:
                links.append(DeploymentSchedule.release_bundle_id == event.release_bundle_id)
            if event.deployment_id is not None:
                links.append(DeploymentSchedule.deployment_id == event.deployment_id)
            schedule = (
                self.db.scalar(
                    select(DeploymentSchedule)
                    .where(or_(*links))
                    .order_by(DeploymentSchedule.created_at.desc())
                )
                if links
                else None
            )
            schedule_recipients = (
                _split_recipients(schedule.notification_recipients) if schedule else []
            )
            attachment_source: DeploymentSchedule | ReleaseBundle | Deployment | None = schedule
            if attachment_source is None and event.release_bundle_id is not None:
                attachment_source = self.db.get(ReleaseBundle, event.release_bundle_id)
            if attachment_source is None and event.deployment_id is not None:
                attachment_source = self.db.get(Deployment, event.deployment_id)
            recipients = schedule_recipients
            if not recipients:
                recipients = default_recipients
            if not recipients:
                cursor.after_created_at = event.created_at
                cursor.after_event_id = event.id
                continue
            label = (
                event.release_bundle_id
                or event.deployment_id
                or event.workflow_instance_id
                or event.id
            )
            status = event.status or event.event_type
            body_lines = [
                f"Release Controller event: {event.event_type}",
                f"Resource: {label}",
                f"Stage: {event.stage}",
                f"Status: {status}",
            ]
            if event.error_code:
                body_lines.append(f"Error code: {event.error_code}")
            if event.message:
                body_lines.append(f"Message: {event.message}")
            try:
                with self.db.begin_nested():
                    self.db.add(
                        EmailOutbox(
                            workflow_event_id=event.id,
                            recipients=",".join(recipients),
                            subject=f"[Release Controller] {status}: {label}",
                            body="\n".join(body_lines),
                            **attachment_values(attachment_source),
                        )
                    )
                    self.db.flush()
                queued += 1
            except IntegrityError:
                # A concurrent dispatcher already persisted this event.
                pass
            cursor.after_created_at = event.created_at
            cursor.after_event_id = event.id
        self.db.commit()
        return queued

    def deliver_pending(self, limit: int = 20) -> int:
        if not self.settings.smtp_host:
            return 0
        stale_before = utc_now() - timedelta(seconds=self.settings.smtp_claim_timeout_seconds)
        row_ids = list(
            self.db.scalars(
                select(EmailOutbox.id)
                .where(
                    or_(
                        EmailOutbox.status.in_(
                            [
                                EmailDeliveryStatus.PENDING.value,
                                EmailDeliveryStatus.FAILED.value,
                            ]
                        ),
                        (EmailOutbox.status == EmailDeliveryStatus.PROCESSING.value)
                        & (EmailOutbox.claimed_at <= stale_before),
                    ),
                    EmailOutbox.attempts < self.settings.smtp_max_attempts,
                    EmailOutbox.next_attempt_at <= utc_now(),
                )
                .order_by(EmailOutbox.created_at.asc())
                .limit(limit)
            ).all()
        )
        sent = 0
        for row_id in row_ids:
            claimed_at = utc_now()
            claimed = self.db.execute(
                update(EmailOutbox)
                .where(
                    EmailOutbox.id == row_id,
                    or_(
                        EmailOutbox.status.in_(
                            [
                                EmailDeliveryStatus.PENDING.value,
                                EmailDeliveryStatus.FAILED.value,
                            ]
                        ),
                        (EmailOutbox.status == EmailDeliveryStatus.PROCESSING.value)
                        & (EmailOutbox.claimed_at <= stale_before),
                    ),
                )
                .values(
                    status=EmailDeliveryStatus.PROCESSING.value,
                    claimed_at=claimed_at,
                    attempts=EmailOutbox.attempts + 1,
                    updated_at=claimed_at,
                )
            )
            self.db.commit()
            if claimed.rowcount != 1:
                continue
            row = self.db.get(EmailOutbox, row_id)
            try:
                self.sender.send(row)
                row.status = EmailDeliveryStatus.SENT.value
                row.sent_at = utc_now()
                row.last_error = None
                row.claimed_at = None
                sent += 1
            except Exception as exc:
                row.status = EmailDeliveryStatus.FAILED.value
                row.last_error = (str(exc) or exc.__class__.__name__)[:2000]
                row.claimed_at = None
                delay = self.settings.smtp_retry_seconds * (2 ** (row.attempts - 1))
                row.next_attempt_at = utc_now() + timedelta(seconds=delay)
            row.updated_at = utc_now()
            self.db.commit()
        return sent

    def list(self, *, limit: int, offset: int) -> tuple[list[EmailOutbox], int]:
        total = self.db.scalar(select(func.count()).select_from(EmailOutbox)) or 0
        items = list(
            self.db.scalars(
                select(EmailOutbox)
                .order_by(EmailOutbox.created_at.desc(), EmailOutbox.id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        )
        return items, total
