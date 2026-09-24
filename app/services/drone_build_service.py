from dataclasses import dataclass

from app.core.config import Settings
from app.db.models.registry import ProjectComponent
from app.integrations.drone.client import DroneClient
from app.integrations.drone.exceptions import DroneNotFoundError
from app.integrations.drone.schemas import DroneBuild, DroneRepositoryInfo
from app.services.upstream_clients import DroneClients, as_drone_clients


class DroneConfigurationError(Exception):
    pass


class BuildNotFoundError(Exception):
    pass


class BuildNotPromotableError(Exception):
    pass


@dataclass(frozen=True)
class DroneRepository:
    owner: str
    name: str

    @classmethod
    def of(cls, component: ProjectComponent) -> "DroneRepository":
        if not (component.drone_owner and component.drone_repo):
            raise DroneConfigurationError(
                f"Component {component.key!r} has no Drone repository configured"
            )
        return cls(owner=component.drone_owner, name=component.drone_repo)


class DroneBuildService:
    """Reads and promotes builds for a component.

    The repository coordinates come from the component row rather than from
    Settings, and the client comes from whichever connection that component
    resolves to.  A bare DroneClient is accepted in place of a provider for
    callers that already hold one.
    """

    def __init__(self, clients: DroneClients | DroneClient, settings: Settings) -> None:
        self.clients = as_drone_clients(clients)
        self.settings = settings

    def client_for(self, component: ProjectComponent) -> DroneClient:
        return self.clients.for_component(component)

    @staticmethod
    def get_drone_repo(component: ProjectComponent) -> DroneRepository:
        return DroneRepository.of(component)

    def list_builds(self, component: ProjectComponent, limit: int) -> list[DroneBuild]:
        repo = DroneRepository.of(component)
        return [
            self._as_build(build)
            for build in self.client_for(component).list_builds(repo.owner, repo.name, limit)
        ]

    def get_repository_info(self, component: ProjectComponent) -> DroneRepositoryInfo:
        repo = DroneRepository.of(component)
        result = self.client_for(component).get_repository(repo.owner, repo.name)
        if isinstance(result, DroneRepositoryInfo):
            return result
        return DroneRepositoryInfo.from_drone(result, repo.owner, repo.name)

    def get_build(self, component: ProjectComponent, build_number: int) -> DroneBuild:
        repo = DroneRepository.of(component)
        try:
            return self._as_build(
                self.client_for(component).get_build(repo.owner, repo.name, build_number)
            )
        except DroneNotFoundError as exc:
            raise BuildNotFoundError from exc

    def validate_promotable(self, component: ProjectComponent, build_number: int) -> DroneBuild:
        build = self.get_build(component, build_number)
        if not build.is_promotable():
            raise BuildNotPromotableError("Build is not promotable")
        return build

    def promote(self, component: ProjectComponent, build_number: int, target: str) -> DroneBuild:
        repo = DroneRepository.of(component)
        return self._as_build(
            self.client_for(component).promote_build(repo.owner, repo.name, build_number, target)
        )

    def find_promotion_build(
        self,
        component: ProjectComponent,
        source_build_number: int,
        target: str,
        *,
        limit: int = 50,
    ) -> DroneBuild | None:
        """Locate a promotion Drone may have created for a request that timed out.

        Returning None means "not present in the recent history", which is not the
        same as "never created" -- the caller keeps the deployment unconfirmed and
        looks again rather than promoting a second time.
        """
        candidates = [
            build
            for build in self.list_builds(component, limit)
            if build.is_promotion_of(source_build_number, target)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda build: build.number)

    @staticmethod
    def _as_build(build: DroneBuild | dict) -> DroneBuild:
        return build if isinstance(build, DroneBuild) else DroneBuild.from_drone(build)
