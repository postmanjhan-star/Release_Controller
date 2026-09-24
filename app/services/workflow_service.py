from functools import lru_cache
from pathlib import Path

from SpiffWorkflow.bpmn.parser.BpmnParser import BpmnParser
from SpiffWorkflow.bpmn.serializer.workflow import BpmnWorkflowSerializer
from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
from SpiffWorkflow.task import TaskState
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.orchestration import Deployment, ReleaseBundle, WorkflowEvent
from app.db.models.workflow import ReleaseWorkflow
from app.domain.orchestration.value_objects import ActorSource
from app.domain.release.entities import Release
from app.domain.release.value_objects import ReleaseStatus
from app.domain.shared.time import utc_now
from app.schemas.workflow import WorkflowStateResponse, WorkflowStepResponse

DEFINITION_ID = "release_approval"
BPMN_PATH = Path(__file__).resolve().parents[1] / "workflows" / "bpmn" / "release_approval.bpmn"

MILESTONE_NAMES = {
    "Task_approval_gate": "Approval gate",
    "Task_start_deployment": "Await deployment",
    "Task_execute_deployment": "Deploy release",
    "EndEvent_rejected": "Rejected",
    "EndEvent_success": "Success",
    "EndEvent_failed": "Failed",
}


class WorkflowStepError(Exception):
    pass


@lru_cache
def get_bpmn_xml() -> str:
    return BPMN_PATH.read_text(encoding="utf-8")


def create_workflow() -> BpmnWorkflow:
    parser = BpmnParser()
    parser.add_bpmn_file(str(BPMN_PATH))
    workflow = BpmnWorkflow(parser.get_spec(DEFINITION_ID))
    workflow.do_engine_steps()
    return workflow


class WorkflowService:
    """v1 核准流程的 SpiffWorkflow instance。

    對外的 release 參數是 domain entity（`app.domain.release.entities.Release`），
    這裡只讀它的 id 與 status——推進流程不需要知道那一列長什麼樣子。
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.serializer = BpmnWorkflowSerializer()

    def create_for_release(self, release_id: str) -> ReleaseWorkflow:
        instance = ReleaseWorkflow(
            release_id=release_id,
            definition_id=DEFINITION_ID,
            state_json=self.serializer.serialize_json(create_workflow()),
        )
        self.db.add(instance)
        return instance

    def complete_step(
        self,
        instance: ReleaseWorkflow,
        element_id: str,
        **data: str,
    ) -> None:
        workflow = self.serializer.deserialize_json(instance.state_json)
        tasks = [
            task
            for task in workflow.get_tasks(state=TaskState.READY)
            if task.task_spec.name == element_id
        ]
        if len(tasks) != 1:
            ready = [task.task_spec.name for task in workflow.get_tasks(state=TaskState.READY)]
            raise WorkflowStepError(
                f"Workflow is not waiting at {element_id}; ready steps: {ready}"
            )
        task = tasks[0]
        if data:
            task.set_data(**data)
        task.run()
        workflow.do_engine_steps()
        instance.state_json = self.serializer.serialize_json(workflow)
        instance.updated_at = utc_now()

    def get_state(self, release: Release) -> WorkflowStateResponse:
        instance, created = self._ensure_for_release(release)
        if created:
            self.db.commit()
            self.db.refresh(instance)
        workflow = self.serializer.deserialize_json(instance.state_json)
        current_ids = [
            task.task_spec.name
            for task in workflow.get_tasks(state=TaskState.READY)
            if task.task_spec.name in MILESTONE_NAMES
        ]
        completed_tasks = {
            task.task_spec.name: task for task in workflow.get_tasks(state=TaskState.COMPLETED)
        }
        completed_ids = set(completed_tasks).intersection(MILESTONE_NAMES)
        completed_ids.update(self._completed_flows(completed_tasks))

        steps = []
        all_tasks = {task.task_spec.name: task for task in workflow.get_tasks()}
        for element_id, name in MILESTONE_NAMES.items():
            task = all_tasks.get(element_id)
            if element_id in current_ids:
                state = "ACTIVE"
            elif element_id in completed_ids:
                state = "COMPLETED"
            elif task is None:
                state = "SKIPPED"
            else:
                state = "PENDING"
            steps.append(WorkflowStepResponse(element_id=element_id, name=name, state=state))

        return WorkflowStateResponse(
            release_id=release.id,
            definition_id=instance.definition_id,
            is_complete=workflow.is_completed(),
            current_element_ids=current_ids,
            completed_element_ids=sorted(completed_ids),
            steps=steps,
            updated_at=instance.updated_at,
        )

    def ensure_for_transition(self, release: Release) -> ReleaseWorkflow:
        instance, _created = self._ensure_for_release(release)
        return instance

    def _ensure_for_release(self, release: Release) -> tuple[ReleaseWorkflow, bool]:
        instance = self.db.scalar(
            select(ReleaseWorkflow).where(ReleaseWorkflow.release_id == release.id)
        )
        if instance is not None:
            return instance, False

        instance = self.create_for_release(release.id)
        if release.status != ReleaseStatus.PENDING:
            decision = "rejected" if release.status == ReleaseStatus.REJECTED else "approved"
            self.complete_step(instance, "Task_approval_gate", decision=decision)
        if release.status in {
            ReleaseStatus.DEPLOYING,
            ReleaseStatus.SUCCESS,
            ReleaseStatus.FAILED,
        }:
            self.complete_step(instance, "Task_start_deployment")
        if release.status in {ReleaseStatus.SUCCESS, ReleaseStatus.FAILED}:
            result = "success" if release.status == ReleaseStatus.SUCCESS else "failed"
            self.complete_step(instance, "Task_execute_deployment", deployment_result=result)
        return instance, True

    @staticmethod
    def _completed_flows(completed_tasks: dict) -> set[str]:
        flows: set[str] = set()
        if "StartEvent_release_created" in completed_tasks:
            flows.add("Flow_start_approval")
        approval = completed_tasks.get("Task_approval_gate")
        if approval is not None:
            flows.add("Flow_approval_decision")
            decision = approval.data.get("decision")
            flows.add(
                "Flow_decision_approved" if decision == "approved" else "Flow_decision_rejected"
            )
        if "Task_start_deployment" in completed_tasks:
            flows.add("Flow_deployment_started")
        deployment = completed_tasks.get("Task_execute_deployment")
        if deployment is not None:
            flows.add("Flow_deployment_result")
            result = deployment.data.get("deployment_result")
            flows.add("Flow_result_success" if result == "success" else "Flow_result_failed")
        return flows


class OrchestrationWorkflowService:
    """Persists the v2.2 stage timeline.

    Every event carries who caused it.  The actor is supplied once when the
    service is constructed rather than passed at each call site, so a new event
    type cannot accidentally be written anonymously.
    """

    def __init__(
        self,
        db: Session,
        *,
        actor: str | None = None,
        actor_source: str = ActorSource.SYSTEM.value,
    ) -> None:
        self.db = db
        self.actor = actor or ActorSource.SYSTEM.value
        self.actor_source = actor_source

    def append(
        self,
        *,
        stage: str,
        event_type: str,
        release: ReleaseBundle | None = None,
        deployment: Deployment | None = None,
        status: str | None = None,
        error_code: str | None = None,
        message: str | None = None,
    ) -> WorkflowEvent:
        event = WorkflowEvent(
            release_bundle_id=release.id
            if release
            else deployment.release_bundle_id
            if deployment
            else None,
            deployment_id=deployment.id if deployment else None,
            workflow_instance_id=(
                release.workflow_instance_id
                if release
                else deployment.release_bundle.workflow_instance_id
                if deployment and deployment.release_bundle
                else None
            ),
            stage=stage,
            event_type=event_type,
            status=status,
            error_code=error_code,
            message=message,
            actor=self.actor,
            actor_source=self.actor_source,
            component=deployment.component if deployment else None,
            target=release.target if release else deployment.target if deployment else None,
        )
        self.db.add(event)
        return event
