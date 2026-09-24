from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.domain.release.exceptions import ReleaseNotFoundError
from app.infrastructure.di.injection import get_get_release_usecase, get_workflow_service
from app.schemas.workflow import WorkflowStateResponse
from app.services.workflow_service import WorkflowService, get_bpmn_xml
from app.usecase.release.get_release_usecase import GetReleaseUseCase

router = APIRouter(tags=["workflows"])
BPMN_DIRECTORY = Path(__file__).resolve().parents[2] / "workflows" / "bpmn"
BPMN_DEFINITIONS = {
    "FRONTEND_ONLY": BPMN_DIRECTORY / "frontend_only.bpmn",
    "BACKEND_ONLY": BPMN_DIRECTORY / "backend_only.bpmn",
    "BUNDLE": BPMN_DIRECTORY / "release_bundle.bpmn",
    "SCHEDULED": BPMN_DIRECTORY / "scheduled_release.bpmn",
}

GetRelease = Annotated[GetReleaseUseCase, Depends(get_get_release_usecase)]


@router.get("/workflows/release-definition", response_class=Response)
def release_workflow_definition() -> Response:
    return Response(content=get_bpmn_xml(), media_type="application/xml")


@router.get("/workflows/{mode}/definition", response_class=Response)
def orchestration_workflow_definition(mode: str) -> Response:
    path = BPMN_DEFINITIONS.get(mode.upper())
    if path is None:
        raise HTTPException(status_code=404, detail="Workflow definition not found")
    return Response(content=path.read_text(encoding="utf-8"), media_type="application/xml")


@router.get(
    "/releases/{release_id}/workflow",
    response_model=WorkflowStateResponse,
)
def release_workflow_state(
    release_id: str,
    usecase: GetRelease,
    workflows: WorkflowService = Depends(get_workflow_service),
) -> WorkflowStateResponse:
    try:
        release = usecase.execute(release_id)
    except ReleaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Release not found") from exc
    return workflows.get_state(release)
