from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.domain.registry.exceptions import (
    ConnectionInUseError,
    ConnectionNameTakenError,
    ConnectionNotFoundError,
)
from app.domain.registry.value_objects import ConnectionKind
from app.infrastructure.di.injection import (
    get_create_connection_usecase,
    get_delete_connection_usecase,
    get_get_connection_usecase,
    get_list_connections_usecase,
    get_test_connection_usecase,
    get_update_connection_usecase,
    require_admin,
)
from app.schemas.registry import (
    ConnectionCreate,
    ConnectionListResponse,
    ConnectionResponse,
    ConnectionTestResponse,
    ConnectionUpdate,
)
from app.usecase.registry.create_connection_usecase import CreateConnectionUseCase
from app.usecase.registry.delete_connection_usecase import DeleteConnectionUseCase
from app.usecase.registry.get_connection_usecase import GetConnectionUseCase
from app.usecase.registry.list_connections_usecase import ListConnectionsUseCase
from app.usecase.registry.test_connection_usecase import TestConnectionUseCase
from app.usecase.registry.update_connection_usecase import UpdateConnectionUseCase

router = APIRouter(prefix="/connections", tags=["connections"])

ListConnections = Annotated[ListConnectionsUseCase, Depends(get_list_connections_usecase)]
GetConnection = Annotated[GetConnectionUseCase, Depends(get_get_connection_usecase)]
CreateConnection = Annotated[CreateConnectionUseCase, Depends(get_create_connection_usecase)]
UpdateConnection = Annotated[UpdateConnectionUseCase, Depends(get_update_connection_usecase)]
DeleteConnection = Annotated[DeleteConnectionUseCase, Depends(get_delete_connection_usecase)]
TestConnection = Annotated[TestConnectionUseCase, Depends(get_test_connection_usecase)]


@router.get("", response_model=ConnectionListResponse)
def list_connections(
    usecase: ListConnections,
    kind: Annotated[ConnectionKind | None, Query()] = None,
) -> ConnectionListResponse:
    return ConnectionListResponse(items=usecase.execute(kind))


@router.post(
    "",
    response_model=ConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_connection(payload: ConnectionCreate, usecase: CreateConnection) -> ConnectionResponse:
    try:
        return usecase.execute(
            kind=payload.kind,
            name=payload.name,
            base_url=payload.base_url,
            token=payload.token,
            is_default=payload.is_default,
        )
    except ConnectionNameTakenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/{connection_id}", response_model=ConnectionResponse)
def get_connection(connection_id: str, usecase: GetConnection) -> ConnectionResponse:
    try:
        return usecase.execute(connection_id)
    except ConnectionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Connection not found") from exc


@router.patch(
    "/{connection_id}",
    response_model=ConnectionResponse,
    dependencies=[Depends(require_admin)],
)
def update_connection(
    connection_id: str, payload: ConnectionUpdate, usecase: UpdateConnection
) -> ConnectionResponse:
    try:
        return usecase.execute(
            connection_id,
            name=payload.name,
            base_url=payload.base_url,
            token=payload.token,
            is_default=payload.is_default,
        )
    except ConnectionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Connection not found") from exc
    except ConnectionNameTakenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete(
    "/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def delete_connection(connection_id: str, usecase: DeleteConnection) -> None:
    try:
        usecase.execute(connection_id)
    except ConnectionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Connection not found") from exc
    except ConnectionInUseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{connection_id}/test", response_model=ConnectionTestResponse)
def test_connection(connection_id: str, usecase: TestConnection) -> ConnectionTestResponse:
    """Ask the upstream whether the stored credential works.

    Always 200: "the token is rejected" is a successful answer to the question,
    and the caller reads it from `status`.
    """
    try:
        result, detail, checked_at = usecase.execute(connection_id)
    except ConnectionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Connection not found") from exc
    return ConnectionTestResponse(status=result, detail=detail, checked_at=checked_at)
