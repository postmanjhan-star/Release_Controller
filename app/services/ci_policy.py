"""Shared stage checks used by CI and the existing deployment tracker."""

from sqlalchemy import select

from app.db.models.ci import CIDeploymentRequest, CIServiceToken
from app.domain.shared.time import utc_now
from app.integrations.drone.schemas import DroneBuild
from app.services.ci_tokens import aware, token_active


def stages_succeeded(build: DroneBuild, names: list[str], *, deployment: bool = False) -> bool:
    if not names:
        return False
    for name in names:
        matches = [stage for stage in build.stages if stage.name == name]
        if len(matches) != 1 or matches[0].status != "success":
            return False
        if deployment:
            steps = matches[0].steps
            # Drone may report a successful implicit clone even when every
            # deployment step was skipped (or the pipeline has no user steps).
            if not any(step.status == "success" and step.name != "clone" for step in steps):
                return False
            if any(step.status not in {"success", "skipped"} for step in steps):
                return False
    return True


def ci_dispatch_allowed(db, deployment, component, registry) -> bool:
    """Recheck the durable CI intent even when recovery/UI sends the promotion."""
    job = db.scalar(
        select(CIDeploymentRequest).where(CIDeploymentRequest.deployment_id == deployment.id)
    )
    if job is None or deployment.requested_by != f"ci-token:{job.service_token_id}":
        # Adopting a human/scheduled intent does not transfer its authorization
        # to the CI credential; its original actor still owns dispatch.
        return True
    db.refresh(component)
    db.refresh(component.project)
    token = db.get(CIServiceToken, job.service_token_id)
    db.refresh(token)
    return (
        token_active(token)
        and aware(job.deadline_at) > utc_now()
        and not component.project.is_archived
        and component.is_active
        and (component.drone_owner, component.drone_repo, registry.drone_connection(component).id)
        == (job.drone_owner, job.drone_repository, job.drone_connection_id)
        and (
            not component.promote_target_override or component.promote_target_override == job.target
        )
    )
