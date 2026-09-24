"""ConnectionRepository 的 SQLAlchemy 實作。"""

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.registry import UpstreamConnection as ConnectionRow
from app.domain.registry.entities import Connection
from app.domain.registry.exceptions import ConnectionNameTakenError, ConnectionNotFoundError
from app.domain.registry.repositories import ConnectionRepository
from app.domain.registry.value_objects import ConnectionKind
from app.infrastructure.sqlite.registry.connection_dto import (
    apply_to_row,
    from_entity,
    to_entity,
)


class SqlAlchemyConnectionRepository(ConnectionRepository):
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, connection: Connection) -> None:
        self.session.add(from_entity(connection))
        self._flush(connection)

    def save(self, connection: Connection) -> None:
        row = self.session.get(ConnectionRow, connection.id)
        if row is None:
            raise ConnectionNotFoundError
        apply_to_row(row, connection)
        self._flush(connection)

    def delete(self, connection: Connection) -> None:
        row = self.session.get(ConnectionRow, connection.id)
        if row is None:
            raise ConnectionNotFoundError
        self.session.delete(row)
        self.session.flush()

    def find_by_id(self, connection_id: str) -> Connection | None:
        row = self.session.get(ConnectionRow, connection_id)
        return None if row is None else to_entity(row)

    def list(self, kind: ConnectionKind | None = None) -> list[Connection]:
        conditions = [ConnectionRow.kind == kind.value] if kind else []
        rows = self.session.scalars(
            select(ConnectionRow)
            .where(*conditions)
            .order_by(ConnectionRow.kind, ConnectionRow.name)
        ).all()
        return [to_entity(row) for row in rows]

    def find_default(self, kind: ConnectionKind) -> Connection | None:
        row = self.session.scalar(
            select(ConnectionRow).where(
                ConnectionRow.kind == kind.value,
                ConnectionRow.is_default.is_(True),
            )
        )
        return None if row is None else to_entity(row)

    def exists_for_kind(self, kind: ConnectionKind) -> bool:
        return (
            self.session.scalar(
                select(ConnectionRow.id).where(ConnectionRow.kind == kind.value).limit(1)
            )
            is not None
        )

    def clear_default(self, kind: ConnectionKind, *, except_id: str | None = None) -> None:
        conditions = [
            ConnectionRow.kind == kind.value,
            ConnectionRow.is_default.is_(True),
        ]
        if except_id:
            conditions.append(ConnectionRow.id != except_id)
        self.session.execute(update(ConnectionRow).where(*conditions).values(is_default=False))
        self.session.flush()

    def _flush(self, connection: Connection) -> None:
        try:
            self.session.flush()
        except IntegrityError as exc:
            # upstream_connections.name 的唯一條件。
            self.session.rollback()
            raise ConnectionNameTakenError(
                f"A connection named {connection.name!r} already exists"
            ) from exc
