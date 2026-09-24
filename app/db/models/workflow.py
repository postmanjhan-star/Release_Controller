from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.release import utc_now


class ReleaseWorkflow(Base):
    __tablename__ = "release_workflows"

    release_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("releases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    definition_id: Mapped[str] = mapped_column(String(100), nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    release: Mapped["Release"] = relationship(back_populates="workflow")


from app.db.models.release import Release  # noqa: E402
