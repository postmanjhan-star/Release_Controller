"""Read models for release operations. No transport or agent-specific business logic."""

from sqlalchemy import func, select

from app.core.crypto import token_cipher
from app.core.redaction import redact
from app.db.models.orchestration import Deployment, ReleaseBundle
from app.db.models.registry import UpstreamConnection
from app.schemas.orchestration import BundleResponse
from app.services.component_registry import ComponentNotRegisteredError, ComponentRegistry
from app.services.release_operations_errors import ErrorCode, MCPToolError
from app.services.release_orchestrator import ReleaseOrchestrator


def version(deployment):
    if deployment is None:
        return None
    return deployment.gitea_release_tag or deployment.version or deployment.commit_sha


class ReleaseOperationsService:
    def __init__(self, db, builds, settings):
        self.db = db
        self.builds = builds
        self.settings = settings
        self.registry = ComponentRegistry(db, settings)
        self.orchestrator = ReleaseOrchestrator(
            db, builds, settings, actor=settings.mcp_actor, actor_source="mcp"
        )

    def redact_output(self, value):
        secrets = [
            str(value)
            for name, value in self.settings.model_dump().items()
            if any(word in name for word in ("token", "password", "secret")) and value
        ]
        cipher = token_cipher(self.settings)
        for ciphertext in self.db.scalars(select(UpstreamConnection.token_encrypted)):
            secrets.append(cipher.decrypt(ciphertext))
        return redact(value, secrets)

    def project(self):
        return self.registry.project(self.settings.mcp_project)

    def component(self, key):
        return self.registry.for_key(key, self.project())

    def target(self, environment):
        if environment not in {"staging", "production"}:
            raise MCPToolError(ErrorCode.INVALID_ARGUMENT, "Unknown environment.")
        return (
            self.settings.mcp_staging_target
            if environment == "staging"
            else self.settings.mcp_production_target
        )

    def latest_deployment(self, component, environment, *, successful=False):
        conditions = [
            Deployment.project_id == self.project().id,
            Deployment.component_id == component.id,
            Deployment.target == self.target(environment),
        ]
        if successful:
            conditions.append(Deployment.status == "SUCCESS")
        return self.db.scalar(
            select(Deployment)
            .where(*conditions)
            .order_by(Deployment.created_at.desc(), Deployment.id.desc())
            .limit(1)
        )

    def get_environment_status(self, environment):
        result = {
            "environment": environment,
            "project_id": self.project().id,
            "health_source": "controller_deployment_state",
            "healthy": None,
            "health_check_supported": False,
        }
        dates = []
        for key in ("frontend", "backend"):
            try:
                component = self.component(key)
            except ComponentNotRegisteredError:
                result[key] = {"version": None, "status": "not_configured"}
                continue
            latest = self.latest_deployment(component, environment)
            deployed = self.latest_deployment(component, environment, successful=True)
            result[key] = {
                "version": version(deployed),
                "commit_sha": deployed.commit_sha if deployed else None,
                "deployment_id": deployed.id if deployed else None,
                "status": latest.status if latest else "unknown",
                "latest_attempt_id": latest.id if latest else None,
                "version_source": "last_successful_deployment",
            }
            if latest:
                dates.append(latest.created_at.isoformat())
        result["last_deployment"] = max(dates) if dates else None
        return result

    def get_release_status(self):
        staging = self.get_environment_status("staging")
        production = self.get_environment_status("production")
        result = {"project_id": self.project().id}
        for key in ("frontend", "backend"):
            result[key] = {
                "staging_version": staging[key]["version"],
                "production_version": production[key]["version"],
                "status": staging[key]["status"],
                "latest_commit": None,
            }
            if staging[key]["status"] != "not_configured":
                builds = self.builds.list_builds(self.component(key), 1)
                result[key]["latest_commit"] = builds[0].commit_sha if builds else None
                result[key]["latest_commit_source"] = "latest_observed_drone_build"
        return result

    def compare_environments(self):
        staging = self.get_environment_status("staging")
        production = self.get_environment_status("production")
        result = {"production_behind": None, "ancestry_supported": False}
        for key in ("frontend", "backend"):
            left, right = staging[key], production[key]
            a, b = left.get("commit_sha"), right.get("commit_sha")
            result[key] = {
                "staging": left["version"],
                "production": right["version"],
                "same": a == b if a and b else None,
            }
        return result

    def list_releases(self, limit=10, offset=0, environment=None, status=None):
        conditions = [ReleaseBundle.project_id == self.project().id]
        if environment:
            conditions.append(ReleaseBundle.target == self.target(environment))
        if status:
            conditions.append(ReleaseBundle.status == status)
        total = self.db.scalar(select(func.count()).select_from(ReleaseBundle).where(*conditions))
        rows = self.db.scalars(
            select(ReleaseBundle)
            .where(*conditions)
            .order_by(ReleaseBundle.created_at.desc(), ReleaseBundle.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return {
            "items": [self.release_view(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "history_model": "release_bundle",
        }

    def release_view(self, row):
        data = BundleResponse.model_validate(row).model_dump(mode="json")
        # Do not expose file data or internal connection configuration.
        data["environment"] = next(
            (env for env in ("staging", "production") if self.target(env) == row.target), None
        )
        for key in ("frontend", "backend"):
            data[f"{key}_version"] = next(
                (version(d) for d in row.deployments if d.component == key), None
            )
        return data

    def get_release(self, release_id):
        row = self.orchestrator.get_bundle(release_id)
        if row.project_id != self.project().id:
            raise MCPToolError(ErrorCode.NOT_FOUND, "Release not found in the configured project.")
        return self.release_view(row)

    def get_pipeline_status(self, repository, build_number):
        build = self.builds.get_build(self.component(repository), build_number)
        result = build.model_dump(mode="json")
        result.update(repository=repository, build_number=build.number, commit=build.commit_sha)
        return result

    def get_repository_state(self, repository):
        component = self.component(repository)
        info = self.builds.get_repository_info(component)
        builds = self.builds.list_builds(component, 1)
        latest = builds[0] if builds else None
        return {
            "repository": repository,
            "default_branch": info.default_branch,
            "latest_commit": None
            if latest is None
            else {
                "sha": latest.commit_sha,
                "message": latest.commit_message,
                "author": latest.author,
                "timestamp": latest.created_at.isoformat() if latest.created_at else None,
            },
            "source": "latest_observed_drone_build",
            "branches_supported": False,
        }
