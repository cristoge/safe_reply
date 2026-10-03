import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from revision_rag.db.base import Base, CreatedAtMixin
from revision_rag.models.enums import MessageRole, MessageStatus, str_enum

if TYPE_CHECKING:
    from revision_rag.models.conversation import Conversation


class Message(CreatedAtMixin, Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[MessageRole] = mapped_column(str_enum(MessageRole, "message_role"))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[MessageStatus] = mapped_column(
        str_enum(MessageStatus, "message_status"),
        default=MessageStatus.COMPLETED,
        server_default=MessageStatus.COMPLETED.value,
    )
    # Copia de lo necesario para mostrar cada fuente (no solo ids), para que el
    # historial no se rompa si se borra el documento citado.
    sources: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    conversation: Mapped[Conversation] = relationship(
        back_populates="messages", lazy="raise"
    )

    __table_args__ = (
        Index(
            "ix_messages_conversation_id_created_at", "conversation_id", "created_at"
        ),
    )
