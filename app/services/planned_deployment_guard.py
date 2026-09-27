"""Keep approved constraints intact when the normal recovery worker advances a bundle."""

import hashlib
import json

from sqlalchemy import select

from app.db.models.mcp import ReleasePlan
from app.services.component_registry import ComponentRegistry
from app.services.release_operations_errors import ErrorCode, MCPToolError


def verify_planned_deployment(db, settings, builds, deployment, component):
    if not deployment.release_bundle_id:
        return
    plan = db.scalar(
        select(ReleasePlan).where(ReleasePlan.release_id == deployment.release_bundle_id)
    )
    if plan is None:
        return
    digest = hashlib.sha256(
        json.dumps(plan.snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    action = next(
        (a for a in plan.snapshot["actions"] if a["component"] == deployment.component), None
    )
    registry = ComponentRegistry(db, settings)
    connection = registry.drone_connection(component)
    route = {
        "component_id": component.id,
        "connection_id": connection.id,
        "server": connection.base_url,
        "owner": component.drone_owner,
        "repository": component.drone_repo,
        "position": component.position,
        "target_override": component.promote_target_override,
    }
    valid = (
        action
        and digest == plan.digest
        and plan.approved_by
        and plan.status in {"executing", "executed", "execution_failed"}
        and plan.project_id == deployment.project_id
        and route == action["route"]
        and component.is_active
        and not component.project.is_archived
        and deployment.target == plan.snapshot["target"]
        and deployment.source_build_number == action["build_number"]
        and deployment.commit_sha == action["commit_sha"]
    )
    if not valid:
        raise MCPToolError(ErrorCode.CONFLICT, "Approved deployment constraints changed.")
    build = builds.validate_promotable(component, deployment.source_build_number)
    if build.commit_sha != action["commit_sha"]:
        raise MCPToolError(ErrorCode.CONFLICT, "Approved source commit changed.")
