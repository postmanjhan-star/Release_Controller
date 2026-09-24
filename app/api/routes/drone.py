from fastapi import APIRouter, Depends

from app.infrastructure.di.injection import get_drone_client
from app.integrations.drone.client import DroneClient
from app.integrations.drone.exceptions import (
    DroneError,
)
from app.schemas.drone import (
    DroneStatusResponse,
)

router = APIRouter(prefix="/drone", tags=["drone"])


@router.get("/status", response_model=DroneStatusResponse)
def drone_status(client: DroneClient = Depends(get_drone_client)) -> DroneStatusResponse:
    try:
        client.status()
        return DroneStatusResponse(status="ok")
    except DroneError:
        return DroneStatusResponse(status="unavailable")
