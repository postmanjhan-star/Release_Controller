from app.integrations.drone.exceptions import DroneError
from app.services.drone_build_service import BuildNotFoundError


class ReleaseDiagnosticsService:
    def __init__(self, operations):
        self.operations = operations

    def get_failed_pipeline_summary(self, repository, build_number):
        component = self.operations.component(repository)
        build = self.operations.builds.get_build(component, build_number)
        failed = [
            {
                "stage": stage.name,
                "stage_number": stage.number,
                "name": step.name,
                "step_number": step.number,
                "exit_code": step.exit_code,
            }
            for stage in build.stages
            for step in stage.steps
            if step.status in {"failure", "error", "failed"} or step.exit_code not in (None, 0)
        ]
        logs, truncated, unavailable = "", False, False
        limit = self.operations.settings.mcp_max_log_length
        # Bound both upstream calls and returned text across all failed steps.
        for step in failed[:5]:
            if len(logs) >= limit:
                truncated = True
                break
            try:
                text = self.operations.builds.get_step_logs(
                    component, build_number, step["stage_number"], step["step_number"]
                )
            except DroneError:
                unavailable = True
                continue
            text = self.operations.redact_output(text)
            remaining = limit - len(logs)
            truncated |= len(text) > remaining
            logs += text[:remaining]
        return {
            "repository": repository,
            "build_number": build_number,
            "status": build.status,
            "failed_steps": failed,
            "logs": logs,
            "log_truncated": truncated or len(failed) > 5,
            "logs_unavailable": unavailable,
            "step_metadata_available": bool(build.stages),
        }

    def diagnose_release(self, release_id):
        release = self.operations.get_release(release_id)
        pipelines, evidence = [], []
        remaining = self.operations.settings.mcp_max_log_length
        for deployment in release["deployments"][:20]:
            number = deployment["promotion_build_number"] or deployment["source_build_number"]
            try:
                summary = self.get_failed_pipeline_summary(deployment["component"], number)
                if len(summary["logs"]) > remaining:
                    summary["log_truncated"] = True
                summary["logs"] = summary["logs"][:remaining]
                remaining -= len(summary["logs"])
                pipelines.append(summary)
            except (DroneError, BuildNotFoundError):
                evidence.append(
                    {"component": deployment["component"], "pipeline_unavailable": True}
                )
            evidence.append(
                {
                    "component": deployment["component"],
                    "status": deployment["status"],
                    "failed_stage": deployment["failed_stage"],
                    "error_code": deployment["error_code"],
                    "error_message": deployment["error_message"],
                }
            )
        return {
            "release": release,
            "pipelines": pipelines,
            "evidence": evidence,
            "pipelines_truncated": len(release["deployments"]) > 20,
        }
