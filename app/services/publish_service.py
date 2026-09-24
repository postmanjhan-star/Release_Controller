import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.orchestration import Deployment, PublishRecord
from app.db.models.registry import ProjectComponent
from app.domain.orchestration.value_objects import PublishRecordStatus, PublishStatus
from app.domain.shared.time import utc_now
from app.integrations.gitea.client import GiteaClient
from app.integrations.gitea.exceptions import (
    GiteaConflictError,
    GiteaError,
    GiteaNotFoundError,
)
from app.integrations.gitea.schemas import GiteaRelease
from app.schemas.publish import PublishRequest
from app.services.component_registry import ComponentRegistry
from app.services.upstream_clients import GiteaClients, as_gitea_clients

logger = logging.getLogger(__name__)


class PublishNotFoundError(Exception):
    pass


class PublishConfigurationError(Exception):
    error_code = "GITEA_REPO_NOT_FOUND"


class TagAlreadyExistsDifferentCommit(Exception):
    error_code = "TAG_ALREADY_EXISTS_DIFFERENT_COMMIT"

    def __init__(self, version: str) -> None:
        super().__init__(f"Tag {version} already exists on a different commit")


@dataclass(frozen=True)
class GiteaRepositoryMapping:
    owner: str
    name: str


@dataclass(frozen=True)
class PublishFilters:
    # A component key, not an enum: a project names its own components.
    component: str | None = None
    status: str | None = None
    version: str | None = None
    release_bundle_id: str | None = None


class PublishService:
    def __init__(
        self,
        db: Session,
        gitea: GiteaClients | GiteaClient,
        settings: Settings,
        *,
        actor: str | None = None,
    ) -> None:
        self.db = db
        self.clients = as_gitea_clients(gitea)
        self.settings = settings
        self.actor = actor
        self.registry = ComponentRegistry(db, settings)

    @staticmethod
    def get_gitea_repo(component: ProjectComponent) -> GiteaRepositoryMapping:
        if not component.publish_enabled:
            raise PublishConfigurationError(
                f"Component {component.key!r} is configured not to publish releases"
            )
        if not (component.gitea_owner and component.gitea_repo):
            raise PublishConfigurationError(
                f"Gitea repository is not configured for {component.key}"
            )
        return GiteaRepositoryMapping(owner=component.gitea_owner, name=component.gitea_repo)

    @staticmethod
    def tag_for(component: ProjectComponent, version: str) -> str:
        """The tag this component claims for a version.

        Two components publishing into one Gitea repository cannot both own
        "v1.2.3", so each carries a prefix; for a component that has a repository
        to itself the prefix is empty and the tag is the version verbatim.
        """
        return f"{component.tag_prefix}{version}"

    def publish_component(
        self,
        deployment: Deployment,
        payload: PublishRequest,
    ) -> PublishRecord:
        component = self.registry.for_deployment(deployment)
        repo = self.get_gitea_repo(component)
        gitea = self.clients.for_component(component)
        record = self._record_for(deployment, payload.version, repo, component)
        if record.status == PublishRecordStatus.PUBLISHED.value:
            self._apply_success(deployment, record)
            record._already_exists = True  # type: ignore[attr-defined]
            return record

        now = utc_now()
        record.status = PublishRecordStatus.PUBLISHING.value
        record.error_code = None
        record.error_message = None
        record.updated_at = now
        deployment.publish_status = PublishStatus.PUBLISHING.value
        deployment.version = payload.version
        deployment.publish_started_at = deployment.publish_started_at or now
        deployment.publish_failed_at = None
        deployment.publish_error_code = None
        deployment.publish_error_message = None
        self.db.flush()
        # Persist the observable PUBLISHING state before making the upstream
        # request. A worker/process interruption can then be retried safely.
        self.db.commit()

        try:
            release, already_exists = self._ensure_release(
                gitea, repo, deployment, payload, record.tag_name
            )
        except TagAlreadyExistsDifferentCommit as exc:
            self._apply_failure(deployment, record, exc.error_code, str(exc))
            raise
        except GiteaError as exc:
            self._apply_failure(
                deployment,
                record,
                getattr(exc, "error_code", "UNKNOWN_PUBLISH_ERROR"),
                str(exc),
            )
            return record

        record.gitea_release_id = str(release.id)
        record.gitea_release_url = release.html_url
        record.status = PublishRecordStatus.PUBLISHED.value
        record.published_at = utc_now()
        record.updated_at = record.published_at
        self._apply_success(deployment, record)
        # This transient attribute lets the orchestrator emit the correct audit event
        # without persisting protocol-specific state.
        record._already_exists = already_exists  # type: ignore[attr-defined]
        logger.info(
            "Gitea release ready deployment_id=%s component=%s repo=%s/%s version=%s commit_sha=%s existing=%s",
            deployment.id,
            deployment.component,
            repo.owner,
            repo.name,
            payload.version,
            deployment.commit_sha,
            already_exists,
        )
        return record

    def get(self, publish_id: str) -> PublishRecord:
        record = self.db.get(PublishRecord, publish_id)
        if record is None:
            raise PublishNotFoundError
        return record

    def list(
        self, filters: PublishFilters, *, limit: int, offset: int
    ) -> tuple[list[PublishRecord], int]:
        conditions = []
        if filters.component:
            conditions.append(PublishRecord.component == filters.component)
        if filters.status:
            conditions.append(PublishRecord.status == filters.status)
        if filters.version:
            conditions.append(PublishRecord.version == filters.version)
        if filters.release_bundle_id:
            conditions.append(PublishRecord.release_bundle_id == filters.release_bundle_id)
        total = (
            self.db.scalar(select(func.count()).select_from(PublishRecord).where(*conditions)) or 0
        )
        items = list(
            self.db.scalars(
                select(PublishRecord)
                .where(*conditions)
                .order_by(PublishRecord.created_at.desc(), PublishRecord.id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        )
        return items, total

    def _record_for(
        self,
        deployment: Deployment,
        version: str,
        repo: GiteaRepositoryMapping,
        component: ProjectComponent,
    ) -> PublishRecord:
        record = self.db.scalar(
            select(PublishRecord).where(
                PublishRecord.deployment_id == deployment.id,
                PublishRecord.version == version,
            )
        )
        if record is not None:
            return record
        record = PublishRecord(
            release_bundle_id=deployment.release_bundle_id,
            deployment_id=deployment.id,
            deployment=deployment,
            project_id=deployment.project_id,
            component_id=component.id,
            component=deployment.component,
            version=version,
            repo_owner=repo.owner,
            repo_name=repo.name,
            commit_sha=deployment.commit_sha,
            tag_name=self.tag_for(component, version),
            status=PublishRecordStatus.PUBLISHING.value,
            requested_by=self.actor,
        )
        self.db.add(record)
        self.db.flush()
        return record

    def _ensure_release(
        self,
        gitea: GiteaClient,
        repo: GiteaRepositoryMapping,
        deployment: Deployment,
        payload: PublishRequest,
        tag_name: str,
    ) -> tuple[GiteaRelease, bool]:
        # Every lookup below uses tag_name, not payload.version: for a component
        # sharing a repository with another the two differ, and reading one while
        # writing the other would let each component overwrite the other's tag.
        # Resolve the repository first so a missing tag is not confused with a
        # missing repository.
        gitea.get_repository(repo.owner, repo.name)
        try:
            tag = gitea.get_tag(repo.owner, repo.name, tag_name)
        except GiteaNotFoundError:
            tag = None
        if tag is not None and tag.commit_sha != deployment.commit_sha:
            raise TagAlreadyExistsDifferentCommit(tag_name)

        if tag is not None:
            try:
                return gitea.get_release_by_tag(repo.owner, repo.name, tag_name), True
            except GiteaNotFoundError:
                pass

        try:
            release = gitea.create_release(
                repo.owner,
                repo.name,
                tag_name,
                deployment.commit_sha,
                payload.name,
                payload.release_notes,
                payload.draft,
                payload.prerelease,
            )
            return release, False
        except GiteaConflictError:
            # Handle a concurrent/idempotent request by re-reading the resulting
            # tag and release. Never move an existing tag.
            tag = gitea.get_tag(repo.owner, repo.name, tag_name)
            if tag.commit_sha != deployment.commit_sha:
                raise TagAlreadyExistsDifferentCommit(tag_name) from None
            return gitea.get_release_by_tag(repo.owner, repo.name, tag_name), True

    @staticmethod
    def _apply_success(deployment: Deployment, record: PublishRecord) -> None:
        deployment.publish_status = PublishStatus.PUBLISHED.value
        deployment.version = record.version
        deployment.gitea_release_id = record.gitea_release_id
        deployment.gitea_release_tag = record.tag_name
        deployment.gitea_release_url = record.gitea_release_url
        deployment.published_at = record.published_at or utc_now()
        deployment.publish_failed_at = None
        deployment.publish_error_code = None
        deployment.publish_error_message = None
        deployment.updated_at = utc_now()

    @staticmethod
    def _apply_failure(
        deployment: Deployment,
        record: PublishRecord,
        error_code: str,
        error_message: str,
    ) -> None:
        now = utc_now()
        record.status = PublishRecordStatus.FAILED.value
        record.error_code = error_code
        record.error_message = error_message
        record.updated_at = now
        deployment.publish_status = PublishStatus.FAILED.value
        deployment.publish_failed_at = now
        deployment.publish_error_code = error_code
        deployment.publish_error_message = error_message
        deployment.updated_at = now
