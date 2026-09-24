from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.infrastructure.di.injection import get_publish_service
from app.schemas.orchestration import PublishRecordListResponse, PublishRecordResponse
from app.services.publish_service import (
    PublishFilters,
    PublishNotFoundError,
    PublishService,
)

router = APIRouter(prefix="/publishes", tags=["publishes"])


@router.get("", response_model=PublishRecordListResponse)
def list_publishes(
    service: PublishService = Depends(get_publish_service),
    component: Annotated[str | None, Query(max_length=50)] = None,
    publish_status: Annotated[str | None, Query(alias="status")] = None,
    version: str | None = None,
    release_bundle_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PublishRecordListResponse:
    items, total = service.list(
        PublishFilters(
            component=component,
            status=publish_status,
            version=version,
            release_bundle_id=release_bundle_id,
        ),
        limit=limit,
        offset=offset,
    )
    return PublishRecordListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{publish_id}", response_model=PublishRecordResponse)
def get_publish(
    publish_id: str,
    service: PublishService = Depends(get_publish_service),
) -> PublishRecordResponse:
    try:
        return service.get(publish_id)
    except PublishNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Publish record not found") from exc
