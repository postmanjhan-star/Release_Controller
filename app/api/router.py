from fastapi import APIRouter, Depends

from app.api.routes import (
    connections,
    deployments,
    drone,
    gitea,
    notifications,
    projects,
    publishes,
    releases,
    schedules,
    workflows,
)
from app.infrastructure.di.injection import require_user

api_router = APIRouter(dependencies=[Depends(require_user)])
api_router.include_router(workflows.router)
api_router.include_router(drone.router)
api_router.include_router(gitea.router)
api_router.include_router(connections.router)
api_router.include_router(projects.router)
api_router.include_router(deployments.router)
api_router.include_router(publishes.router)
api_router.include_router(schedules.router)
api_router.include_router(notifications.router)
api_router.include_router(releases.router, prefix="/releases", tags=["releases"])
