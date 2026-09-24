from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.infrastructure.di.injection import get_gitea_client
from app.integrations.gitea.client import GiteaClient
from app.integrations.gitea.exceptions import GiteaError
from app.schemas.gitea import GiteaHealthResponse

router = APIRouter(prefix="/gitea", tags=["gitea"])


@router.get("/health", response_model=GiteaHealthResponse)
def gitea_health(
    client: GiteaClient = Depends(get_gitea_client),
) -> GiteaHealthResponse | JSONResponse:
    try:
        client.health()
    except GiteaError as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unavailable",
                "server": client.server,
                "endpoint": client.health_url,
                "detail": str(exc),
            },
        )
    return GiteaHealthResponse(
        status="ok",
        server=client.server,
        endpoint=client.health_url,
    )


@router.get("/status")
def gitea_status(client: GiteaClient = Depends(get_gitea_client)) -> dict[str, str]:
    try:
        client.health()
    except GiteaError:
        return {"status": "unavailable"}
    return {"status": "ok"}
