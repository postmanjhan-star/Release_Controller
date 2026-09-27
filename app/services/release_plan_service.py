"""Planning is side-effect free for deployments; execution uses durable approval."""

import hashlib
import json
import uuid
from datetime import timedelta, timezone

from sqlalchemy import update

from app.db.models.mcp import ReleasePlan
from app.domain.shared.time import utc_now
from app.schemas.orchestration import BundlePromoteRequest, ComponentBuildSelection
from app.services.release_operations_errors import ErrorCode, MCPToolError
from app.services.release_operations_service import version


def snapshot_digest(snapshot):
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ReleasePlanService:
    def __init__(self, operations):
        self.ops = operations
        self.db = operations.db
        self.settings = operations.settings

    def route(self, component):
        connection = self.ops.registry.drone_connection(component)
        return {
            "component_id": component.id,
            "connection_id": connection.id,
            "server": connection.base_url,
            "owner": component.drone_owner,
            "repository": component.drone_repo,
            "position": component.position,
            "target_override": component.promote_target_override,
        }

    def candidate(self, key):
        component = self.ops.component(key)
        staged = self.ops.latest_deployment(component, "staging", successful=True)
        latest = self.ops.latest_deployment(component, "staging")
        production = self.ops.latest_deployment(component, "production", successful=True)
        build = self.ops.builds.get_build(component, staged.source_build_number) if staged else None
        return {
            "candidate": version(staged),
            "commit_sha": staged.commit_sha if staged else None,
            "build_number": staged.source_build_number if staged else None,
            "pipeline_passed": bool(
                build and build.is_promotable() and build.commit_sha == staged.commit_sha
            ),
            "currently_in_staging": staged is not None,
            "currently_in_production": bool(
                staged and production and staged.commit_sha == production.commit_sha
            ),
            "staging_deployment_succeeded": bool(latest and latest.status == "SUCCESS"),
            "staging_deployment_id": staged.id if staged else None,
        }

    def get_release_candidates(self):
        return {key: self.candidate(key) for key in ("frontend", "backend")}

    def snapshot(self, target_environment, frontend, backend):
        actions, blockers, checks = [], [], {}
        target = self.ops.target(target_environment)
        project = self.ops.project()
        if project.is_archived:
            blockers.append("Project is archived.")
        for key, selected in [("frontend", frontend), ("backend", backend)]:
            if not selected:
                continue
            component = self.ops.component(key)
            route = self.route(component)
            if not component.is_active:
                blockers.append(f"{key}: component is inactive.")
            # The orchestrator honors overrides. Refuse a different effective
            # target, including staging -> production; never silently ignore it.
            if component.promote_target_override not in (None, "", target):
                blockers.append(f"{key}: target override differs from the requested environment.")
            previous = self.ops.latest_deployment(component, target_environment, successful=True)
            if target_environment == "production":
                candidate = self.candidate(key)
                number, commit = candidate["build_number"], candidate["commit_sha"]
                destination = candidate["candidate"]
                passed = candidate["pipeline_passed"]
                checks[f"{key}_staging_deployment_succeeded"] = candidate[
                    "staging_deployment_succeeded"
                ]
                if not candidate["staging_deployment_succeeded"]:
                    blockers.append(f"{key}: latest staging deployment has not succeeded.")
                source_id = candidate["staging_deployment_id"]
            else:
                info = self.ops.builds.get_repository_info(component)
                builds = self.ops.builds.list_builds(component, 50)
                build = next(
                    (
                        b
                        for b in builds
                        if b.event == "push"
                        and info.default_branch
                        and b.branch == info.default_branch
                    ),
                    None,
                )
                number, commit = (build.number, build.commit_sha) if build else (None, None)
                destination = commit
                passed = bool(build and build.is_promotable())
                source_id = None
            checks[f"{key}_pipeline_passed"] = passed
            if not passed or not commit:
                blockers.append(f"{key}: no successful source build with a verified commit.")
            if previous and previous.commit_sha == commit:
                blockers.append(f"{key}: target already has this commit.")
            actions.append(
                {
                    "component": key,
                    "from": version(previous),
                    "to": destination,
                    "from_deployment_id": previous.id if previous else None,
                    "source_deployment_id": source_id,
                    "build_number": number,
                    "commit_sha": commit,
                    "route": route,
                }
            )
        if not actions:
            raise MCPToolError(ErrorCode.INVALID_ARGUMENT, "Select at least one component.")
        return {
            "project_id": project.id,
            "target_environment": target_environment,
            "target": target,
            "actions": actions,
            "checks": checks,
            "requires_approval": target_environment == "production",
            "executable": not blockers,
            "blockers": blockers,
            "health_check_supported": False,
        }

    def create_release_plan(self, target_environment="production", frontend=True, backend=True):
        snapshot = self.snapshot(target_environment, frontend, backend)
        plan = ReleasePlan(
            project_id=snapshot["project_id"],
            snapshot=snapshot,
            digest=snapshot_digest(snapshot),
            created_by=self.settings.mcp_actor,
            expires_at=utc_now() + timedelta(seconds=self.settings.mcp_plan_ttl_seconds),
        )
        self.db.add(plan)
        self.db.commit()
        return self.view(plan)

    def get_release_plan(self, plan_id):
        return self.view(self.get(plan_id))

    def get(self, plan_id):
        plan = self.db.get(ReleasePlan, plan_id)
        if plan is None or plan.project_id != self.ops.project().id:
            raise MCPToolError(ErrorCode.NOT_FOUND, "Release plan not found.")
        return plan

    @staticmethod
    def view(plan):
        return {
            **plan.snapshot,
            "plan_id": plan.id,
            "digest": plan.digest,
            "approval_status": plan.status,
            "approved_by": plan.approved_by,
            "expires_at": plan.expires_at.isoformat(),
            "release_id": plan.release_id,
        }

    def validate_current(self, plan):
        expires = (
            plan.expires_at.replace(tzinfo=timezone.utc)
            if plan.expires_at.tzinfo is None
            else plan.expires_at
        )
        if expires <= utc_now():
            raise MCPToolError(ErrorCode.CONFLICT, "Release plan expired. Create a new plan.")
        if snapshot_digest(plan.snapshot) != plan.digest:
            raise MCPToolError(ErrorCode.CONFLICT, "Release plan integrity check failed.")
        keys = {a["component"] for a in plan.snapshot["actions"]}
        current = self.snapshot(
            plan.snapshot["target_environment"], "frontend" in keys, "backend" in keys
        )
        if current != plan.snapshot:
            raise MCPToolError(
                ErrorCode.CONFLICT, "Versions or configuration changed. Create a new plan."
            )
        if not current["executable"]:
            raise MCPToolError(ErrorCode.PIPELINE_FAILED, "Release plan has unresolved blockers.")

    def approve(self, plan_id, digest, user):
        # Stricter than the optional registry admin guard. Development's synthetic
        # user and an MCP bearer credential can never approve production.
        if (
            not self.settings.auth_enabled
            or not user.is_admin
            or user.login == "authentication-disabled"
        ):
            raise MCPToolError(
                ErrorCode.PERMISSION_DENIED, "An authenticated administrator must approve the plan."
            )
        plan = self.get(plan_id)
        if plan.snapshot["target_environment"] != "production" or digest != plan.digest:
            raise MCPToolError(
                ErrorCode.CONFLICT, "Review the exact production plan digest before approval."
            )
        self.validate_current(plan)
        updated = self.db.execute(
            update(ReleasePlan)
            .execution_options(synchronize_session=False)
            .where(
                ReleasePlan.id == plan.id,
                ReleasePlan.status == "pending",
                ReleasePlan.digest == digest,
                ReleasePlan.expires_at > utc_now(),
            )
            .values(status="approved", approved_by=user.login, approved_at=utc_now())
        )
        if updated.rowcount != 1:
            self.db.rollback()
            raise MCPToolError(ErrorCode.CONFLICT, "Plan is no longer awaiting approval.")
        self.db.commit()
        self.db.refresh(plan)
        return self.view(plan)

    def execute(self, snapshot, *, release_id=None):
        payload = BundlePromoteRequest(
            project_id=snapshot["project_id"],
            target=snapshot["target"],
            requested_by=self.settings.mcp_actor,
            components=[
                ComponentBuildSelection(key=a["component"], build_number=a["build_number"])
                for a in snapshot["actions"]
            ],
        )
        bundle = self.ops.orchestrator.create_bundle(
            payload,
            bundle_id=release_id,
            expected_commits={a["component"]: a["commit_sha"] for a in snapshot["actions"]},
            required_target=snapshot["target"],
        )
        return {
            "deployment_id": bundle.id,
            "release_id": bundle.id,
            "environment": snapshot["target_environment"],
            "status": bundle.status,
        }

    def deploy_staging(self, frontend, backend, reason):
        if not self.settings.mcp_enabled or self.settings.mcp_mode not in {"staging", "full"}:
            raise MCPToolError(ErrorCode.PERMISSION_DENIED, "Staging deployment is not permitted.")
        snapshot = self.snapshot("staging", frontend, backend)
        if not snapshot["executable"]:
            raise MCPToolError(
                ErrorCode.CONFLICT,
                "Staging plan has unresolved blockers.",
                {"blockers": snapshot["blockers"]},
            )
        return self.execute(snapshot)

    def promote_to_production(self, plan_id, reason):
        if not self.settings.mcp_enabled or self.settings.mcp_mode != "full":
            raise MCPToolError(ErrorCode.PERMISSION_DENIED, "Production requires full mode.")
        plan = self.get(plan_id)
        if plan.status != "approved" or not plan.approved_by:
            raise MCPToolError(
                ErrorCode.PERMISSION_DENIED, "Production requires a server-side approved plan."
            )
        if plan.snapshot["target_environment"] != "production":
            raise MCPToolError(
                ErrorCode.PERMISSION_DENIED, "Only production plans can be promoted."
            )
        self.validate_current(plan)
        release_id = str(uuid.uuid4())
        claim = self.db.execute(
            update(ReleasePlan)
            .execution_options(synchronize_session=False)
            .where(
                ReleasePlan.id == plan.id,
                ReleasePlan.status == "approved",
                ReleasePlan.digest == plan.digest,
                ReleasePlan.expires_at > utc_now(),
            )
            .values(status="executing", claimed_at=utc_now(), release_id=release_id)
        )
        if claim.rowcount != 1:
            self.db.rollback()
            raise MCPToolError(ErrorCode.CONFLICT, "Approval has already been consumed.")
        # Durable, one-use claim before any upstream write, never held across I/O.
        self.db.commit()
        self.db.refresh(plan)
        try:
            result = self.execute(plan.snapshot, release_id=release_id)
        except Exception:
            self.db.rollback()
            self.db.execute(
                update(ReleasePlan)
                .execution_options(synchronize_session=False)
                .where(ReleasePlan.id == plan.id)
                .values(status="execution_failed")
            )
            self.db.commit()
            self.db.refresh(plan)
            raise
        self.db.execute(
            update(ReleasePlan).where(ReleasePlan.id == plan.id).values(status="executed")
        )
        self.db.commit()
        return {**result, "plan_id": plan.id}
