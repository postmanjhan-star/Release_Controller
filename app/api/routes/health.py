from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db

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
            },
        )
    return {"status": "ok", "service": settings.app_name, "database": "ok"}
