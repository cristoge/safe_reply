import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from revision_rag.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from revision_rag.models.conversation import Conversation
    from revision_rag.models.document import Document
    from revision_rag.models.study_material import StudyMaterial
    from revision_rag.models.user import User


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="projects", lazy="raise")
    documents: Mapped[list[Document]] = relationship(
        back_populates="project", lazy="raise", passive_deletes=True
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="project", lazy="raise", passive_deletes=True
    )
    study_materials: Mapped[list[StudyMaterial]] = relationship(
        back_populates="project", lazy="raise", passive_deletes=True
    )

    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name="name_not_empty"),)
