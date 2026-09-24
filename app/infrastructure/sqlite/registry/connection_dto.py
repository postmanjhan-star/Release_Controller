"""Connection entity 與 upstream_connections 資料列之間的轉換。"""

from app.db.models.registry import UpstreamConnection as ConnectionRow
from app.domain.registry.entities import Connection
from app.domain.registry.value_objects import (
    ConnectionKind,
    ConnectionVerifyStatus,
    EncryptedToken,
)

# 建立之後還會變的欄位。kind 不在裡面：一條 Drone 連線不會變成 Gitea 連線。
MUTABLE_FIELDS = (
    "name",
    "base_url",
    "token_encrypted",
    "token_hint",
    "is_default",
    "verify_status",
    "verify_detail",
    "verified_at",
    "updated_at",
)


def to_entity(row: ConnectionRow) -> Connection:
    return Connection(
        id=row.id,
        kind=ConnectionKind(row.kind),
        name=row.name,
        base_url=row.base_url,
        token=EncryptedToken(cipher_text=row.token_encrypted, hint=row.token_hint),
        is_default=row.is_default,
        created_at=row.created_at,
        updated_at=row.updated_at,
        verify_status=(
            None if row.verify_status is None else ConnectionVerifyStatus(row.verify_status)
        ),
        verify_detail=row.verify_detail,
        verified_at=row.verified_at,
    )


def from_entity(connection: Connection) -> ConnectionRow:
    row = ConnectionRow(
        id=connection.id,
        kind=connection.kind.value,
        created_at=connection.created_at,
    )
    apply_to_row(row, connection)
    return row


def apply_to_row(row: ConnectionRow, connection: Connection) -> None:
    row.name = connection.name
    row.base_url = connection.base_url
    row.token_encrypted = connection.token_encrypted
    row.token_hint = connection.token_hint
    row.is_default = connection.is_default
    row.verify_status = None if connection.verify_status is None else connection.verify_status.value
    row.verify_detail = connection.verify_detail
    row.verified_at = connection.verified_at
    row.updated_at = connection.updated_at
