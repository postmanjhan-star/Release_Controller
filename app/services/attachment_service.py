import mimetypes
from dataclasses import dataclass
from typing import TypeVar

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, ValidationError
from starlette.datastructures import UploadFile

from app.core.config import Settings

PayloadModel = TypeVar("PayloadModel", bound=BaseModel)

OFFICE_CONTENT_TYPES = {
    ".csv": "text/csv",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".docm": "application/vnd.ms-word.document.macroEnabled.12",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pptm": "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
}


@dataclass(frozen=True)
class UploadedAttachment:
    filename: str
    content_type: str
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)


def apply_attachment(record: object, attachment: UploadedAttachment | None) -> None:
    if attachment is None:
        return
    record.attachment_filename = attachment.filename
    record.attachment_content_type = attachment.content_type
    record.attachment_size = attachment.size
    record.attachment_data = attachment.data


def attachment_values(record: object | None) -> dict[str, object | None]:
    if record is None or not getattr(record, "attachment_filename", None):
        return {}
    return {
        "attachment_filename": record.attachment_filename,
        "attachment_content_type": record.attachment_content_type,
        "attachment_size": record.attachment_size,
        "attachment_data": record.attachment_data,
    }


async def parse_payload_with_optional_attachment(
    request: Request,
    model_type: type[PayloadModel],
    settings: Settings,
    *,
    allow_empty_payload: bool = False,
) -> tuple[PayloadModel, UploadedAttachment | None]:
    content_type = request.headers.get("content-type", "").casefold()
    attachment: UploadedAttachment | None = None
    try:
        if content_type.startswith("multipart/form-data"):
            form = await request.form(max_part_size=settings.notification_attachment_max_bytes)
            try:
                raw_payload = form.get("payload")
                if raw_payload is None and allow_empty_payload:
                    payload = model_type()
                elif not isinstance(raw_payload, str):
                    raise ValueError("Multipart field 'payload' must contain JSON")
                else:
                    payload = model_type.model_validate_json(raw_payload)

                upload = form.get("attachment")
                if upload is not None:
                    if not isinstance(upload, UploadFile):
                        raise ValueError("Multipart field 'attachment' must be a file")
                    attachment = await _read_attachment(upload, settings)
            finally:
                await form.close()
        else:
            body = await request.body()
            if not body and allow_empty_payload:
                payload = model_type()
            else:
                payload = model_type.model_validate_json(body)
    except ValidationError as exc:
        details = [
            {"type": error["type"], "loc": error["loc"], "msg": error["msg"]}
            for error in exc.errors(include_url=False)
        ]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=details,
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return payload, attachment


async def _read_attachment(upload: UploadFile, settings: Settings) -> UploadedAttachment:
    try:
        raw_filename = (upload.filename or "").strip()
        filename = raw_filename.replace("\\", "/").rsplit("/", 1)[-1]
        if (
            not filename
            or len(filename) > 255
            or any(character in filename for character in "\r\n\0")
        ):
            raise ValueError("Attachment filename is invalid")

        supplied_content_type = (upload.content_type or "").split(";", 1)[0].strip()
        guessed_content_type, _encoding = mimetypes.guess_type(filename)
        suffix = f".{filename.rsplit('.', 1)[-1].casefold()}" if "." in filename else ""
        content_type = OFFICE_CONTENT_TYPES.get(suffix)
        if content_type is None:
            content_type = (
                guessed_content_type
                if guessed_content_type
                and supplied_content_type in {"", "application/octet-stream"}
                else supplied_content_type or "application/octet-stream"
            )
        if (
            not content_type
            or len(content_type) > 255
            or "/" not in content_type
            or any(character in content_type for character in "\r\n")
        ):
            content_type = "application/octet-stream"

        data = await upload.read(settings.notification_attachment_max_bytes + 1)
        if not data:
            raise ValueError("Attachment must not be empty")
        if len(data) > settings.notification_attachment_max_bytes:
            size_limit = settings.notification_attachment_max_bytes
            display_limit = (
                f"{size_limit // (1024 * 1024)} MB"
                if size_limit >= 1024 * 1024
                else f"{size_limit} bytes"
            )
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Attachment exceeds the {display_limit} limit",
            )
        return UploadedAttachment(filename=filename, content_type=content_type, data=data)
    finally:
        await upload.close()
