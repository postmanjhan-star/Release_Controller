"""Discover repositories through an explicit or default registry connection."""

from app.core.config import Settings
from app.domain.registry.value_objects import ConnectionKind
from app.integrations.factory import build_drone_client_for, build_gitea_client_for
from app.integrations.repositories import RepositoryMatch
from app.usecase.registry.resolve_connection_usecase import ResolveConnectionUseCase


class RepositoryCatalog:
    def __init__(self, resolve_connection: ResolveConnectionUseCase, settings: Settings) -> None:
        self.resolve_connection = resolve_connection
        self.settings = settings

    def search(
        self,
        kind: ConnectionKind,
        connection_id: str | None,
        query: str,
        page: int,
        limit: int,
    ) -> tuple[str, list[RepositoryMatch], bool]:
        connection = self.resolve_connection.execute(kind, connection_id)
        # The resolver also serves older callers that do not check the kind.
        # Never send one provider's credential to the other provider's API.
        if connection.kind != kind:
            raise ValueError("Connection kind does not match the repository source")
        if kind == ConnectionKind.DRONE:
            repositories = build_drone_client_for(connection, self.settings).list_repositories()
            query = query.casefold()
            repositories = sorted(
                (item for item in repositories if query in item.full_name.casefold()),
                key=lambda item: item.full_name.casefold(),
            )
            start = (page - 1) * limit
            return (
                connection.id,
                repositories[start : start + limit],
                len(repositories) > start + limit,
            )
        repositories = build_gitea_client_for(connection, self.settings).search_repositories(
            query, page, limit
        )
        return connection.id, repositories, len(repositories) == limit
