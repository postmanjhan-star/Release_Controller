from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.domain.scheduling.value_objects import ScheduleStatus
from app.infrastructure.di.injection import get_build_service, get_schedule_service, require_user
from app.schemas.scheduling import (
    RunDueResponse,
    ScheduleCreate,
    ScheduleListResponse,
    ScheduleResponse,
)
from app.services.attachment_service import parse_payload_with_optional_attachment
from app.services.auth_service import CurrentUser, verified_actor
from app.services.drone_build_service import DroneBuildService
from app.services.schedule_service import (
    InvalidScheduleTransitionError,
    ScheduleNotFoundError,
    ScheduleRunner,
    ScheduleService,
)

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.post("", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    request: Request,
    service: ScheduleService = Depends(get_schedule_service),
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(require_user),
) -> ScheduleResponse:
    payload, attachment = await parse_payload_with_optional_attachment(
        request, ScheduleCreate, settings
    )
    return service.create(
        payload.model_copy(update={"requested_by": verified_actor(user, payload.requested_by)}),
        attachment=attachment,
    )


@router.get("", response_model=ScheduleListResponse)
def list_schedules(
    service: ScheduleService = Depends(get_schedule_service),
    schedule_status: Annotated[ScheduleStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ScheduleListResponse:
    items, total = service.list(status=schedule_status, limit=limit, offset=offset)
    return ScheduleListResponse(items=items, total=total, limit=limit, offset=offset)


@router.post("/run-due", response_model=RunDueResponse)
def run_due_schedules(
    db: Session = Depends(get_db),
    builds: DroneBuildService = Depends(get_build_service),
    settings: Settings = Depends(get_settings),
) -> RunDueResponse:
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    # The runner takes the session it works in; this endpoint runs the schedules
    # inside the request, so the request's build service is the right one.
    processed = ScheduleRunner(factory, lambda _session: builds, settings).run_due()
    return RunDueResponse(processed=processed)


@router.get("/{schedule_id}", response_model=ScheduleResponse)
def get_schedule(
    schedule_id: str,
    service: ScheduleService = Depends(get_schedule_service),
) -> ScheduleResponse:
    try:
        return service.get(schedule_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Schedule not found") from exc


@router.delete("/{schedule_id}", response_model=ScheduleResponse)
def cancel_schedule(
    schedule_id: str,
    service: ScheduleService = Depends(get_schedule_service),
) -> ScheduleResponse:
    try:
        return service.cancel(schedule_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Schedule not found") from exc
    except InvalidScheduleTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
