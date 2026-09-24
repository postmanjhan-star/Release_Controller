"""Which upstream client a given component talks to.

A component can name its own connection, inherit its project's, or fall back to
the default one.  That choice has to be made per call rather than once per
request, because a single release may span components on different Drone
instances.  These providers are the seam: everything on the deployment path asks
a provider for a client instead of holding one.

FixedDroneClients/FixedGiteaClients are the honest degenerate case -- one client
for everything.  That is what a test injects, and what a caller that already
holds a client passes in.
"""

from __future__ import annotations

from typing import Protocol

from app.core.config import Settings
from app.db.models.registry import ProjectComponent
from app.integrations.drone.client import DroneClient
from app.integrations.factory import build_drone_client_for, build_gitea_client_for
from app.integrations.gitea.client import GiteaClient
from app.services.component_registry import ComponentRegistry


class DroneClients(Protocol):
    def for_component(self, component: ProjectComponent) -> DroneClient: ...


class GiteaClients(Protocol):
    def for_component(self, component: ProjectComponent) -> GiteaClient: ...


class FixedDroneClients:
    def __init__(self, client: DroneClient) -> None:
        self._client = client

    def for_component(self, component: ProjectComponent) -> DroneClient:
        return self._client


class FixedGiteaClients:
    def __init__(self, client: GiteaClient) -> None:
        self._client = client

    def for_component(self, component: ProjectComponent) -> GiteaClient:
        return self._client


class RegistryDroneClients:
    """A client per connection, built once and reused for the rest of the request."""

    def __init__(self, registry: ComponentRegistry, settings: Settings) -> None:
        self.registry = registry
        self.settings = settings
        self._cache: dict[str, DroneClient] = {}

    def for_component(self, component: ProjectComponent) -> DroneClient:
        connection = self.registry.drone_connection(component)
        if connection.id not in self._cache:
            self._cache[connection.id] = build_drone_client_for(connection, self.settings)
        return self._cache[connection.id]


class RegistryGiteaClients:
    def __init__(self, registry: ComponentRegistry, settings: Settings) -> None:
        self.registry = registry
        self.settings = settings
        self._cache: dict[str, GiteaClient] = {}

    def for_component(self, component: ProjectComponent) -> GiteaClient:
        connection = self.registry.gitea_connection(component)
        if connection.id not in self._cache:
            self._cache[connection.id] = build_gitea_client_for(connection, self.settings)
        return self._cache[connection.id]


def as_drone_clients(clients: DroneClients | DroneClient) -> DroneClients:
    """Accept a bare client where a provider is expected.

    Callers that already hold one client -- tests, and anything constructing a
    service directly -- should not have to know the provider exists.
    """
    return clients if hasattr(clients, "for_component") else FixedDroneClients(clients)


def as_gitea_clients(clients: GiteaClients | GiteaClient) -> GiteaClients:
    return clients if hasattr(clients, "for_component") else FixedGiteaClients(clients)
