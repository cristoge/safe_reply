import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from revision_rag.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from revision_rag.models.message import Message
    from revision_rag.models.project import Project


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    # Se genera a partir de la primera pregunta.
    title: Mapped[str | None] = mapped_column(String(200))

    project: Mapped[Project] = relationship(
        back_populates="conversations", lazy="raise"
    )
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", lazy="raise", passive_deletes=True
    )

    # updated_at = última actividad: la lista de chats se ordena por ella.
    # El índice compuesto también cubre las búsquedas solo por project_id.
    __table_args__ = (
        Index("ix_conversations_project_id_updated_at", "project_id", "updated_at"),
    )
