import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.api.routes.auth import router as auth_router
from app.api.routes.health import router as health_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.services.background_worker import BackgroundWorker
from app.services.component_registry import ComponentRegistry
from app.services.drone_build_service import DroneBuildService
from app.services.upstream_clients import RegistryDroneClients

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)
static_directory = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Application startup environment=%s", settings.app_env)
    task = None
    if settings.background_worker_enabled:

        def build_service(db) -> DroneBuildService:
            registry = ComponentRegistry(db, settings)
            return DroneBuildService(RegistryDroneClients(registry, settings), settings)

        worker = BackgroundWorker(SessionLocal, build_service, settings)
        try:
            worker.reconcile_on_startup()
        except Exception:
            # Recovery must never keep the service from coming up; the periodic
            # sweep picks the same work up on the next tick.
            logger.exception("Startup recovery failed")
        task = asyncio.create_task(worker.run(), name="release-controller-background-worker")
        logger.info("Background scheduler, recovery and notification worker started")
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def create_app() -> FastAPI:
    application = FastAPI(title=settings.app_name, lifespan=lifespan)

    @application.exception_handler(Exception)
    async def unexpected_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unexpected exception", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    application.include_router(health_router)
    application.include_router(auth_router, prefix="/api/v1")
    application.include_router(api_router, prefix="/api/v1")

    @application.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def missing_api_route(path: str) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": f"API endpoint not found: /api/{path}"},
        )

    if static_directory.exists():
        application.mount(
            "/",
            StaticFiles(directory=static_directory, html=True),
            name="frontend",
        )
    return application


app = create_app()
