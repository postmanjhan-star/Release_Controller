from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.schemas.scheduling import (
    EmailOutboxListResponse,
    NotificationDispatchResponse,
    NotificationRecipientCreate,
    NotificationRecipientListResponse,
    NotificationRecipientResponse,
)
from app.services.notification_service import (
    DuplicateNotificationRecipientError,
    NotificationRecipientNotFoundError,
    NotificationRecipientService,
    NotificationService,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/recipients", response_model=NotificationRecipientListResponse)
def list_recipients(db: Session = Depends(get_db)) -> NotificationRecipientListResponse:
    items = NotificationRecipientService(db).list()
    return NotificationRecipientListResponse(items=items, total=len(items))


@router.post(
    "/recipients",
    response_model=NotificationRecipientResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_recipient(
    payload: NotificationRecipientCreate, db: Session = Depends(get_db)
) -> NotificationRecipientResponse:
    try:
        return NotificationRecipientService(db).create(payload.email)
    except DuplicateNotificationRecipientError as exc:
        raise HTTPException(status_code=409, detail="Email recipient already exists") from exc


@router.delete("/recipients/{recipient_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recipient(recipient_id: str, db: Session = Depends(get_db)) -> Response:
    try:
        NotificationRecipientService(db).delete(recipient_id)
    except NotificationRecipientNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Email recipient not found") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/outbox", response_model=EmailOutboxListResponse)
def list_outbox(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EmailOutboxListResponse:
    items, total = NotificationService(db, settings).list(limit=limit, offset=offset)
    return EmailOutboxListResponse(items=items, total=total, limit=limit, offset=offset)


@router.post("/dispatch", response_model=NotificationDispatchResponse)
def dispatch_notifications(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> NotificationDispatchResponse:
    service = NotificationService(db, settings)
    queued = service.enqueue_new_events()
    sent = service.deliver_pending()
    return NotificationDispatchResponse(queued=queued, sent=sent)
