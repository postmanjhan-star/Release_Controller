import uuid

from app.db.models.mcp import MCPToolInvocation
from app.domain.shared.time import utc_now


class MCPAuditService:
    """Separate short transactions; audit failure prevents a tool from starting."""

    def __init__(self, sessions):
        self.sessions = sessions

    def record(
        self, *, tool, arguments, actor, client, status, invocation_id=None, started_at=None
    ):
        invocation_id = invocation_id or str(uuid.uuid4())
        started_at = started_at or utc_now()
        with self.sessions() as db:
            db.add(
                MCPToolInvocation(
                    invocation_id=invocation_id,
                    tool_name=tool[:100],
                    arguments=arguments,
                    actor=actor,
                    client=client[:100],
                    result_status=status,
                    started_at=started_at,
                    finished_at=None if status == "started" else utc_now(),
                )
            )
            db.commit()
        return invocation_id, started_at
