from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.domain.shared.time import utc_now

router = APIRouter(tags=["health"])


@router.get("/health", response_model=None)
def health(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str] | JSONResponse:
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "error",
                "service": settings.app_name,
                "database": "unavailable",
                "time": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
    return {
        "status": "ok",
        "service": settings.app_name,
        "database": "ok",
        "time": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
