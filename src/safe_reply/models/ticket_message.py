import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from safe_reply.db.base import Base, CreatedAtMixin
from safe_reply.models.enums import MessageAuthor, MessageStatus, str_enum
from safe_reply.models.ticket import TICKET_TEXT_MAX_LENGTH

if TYPE_CHECKING:
    from safe_reply.models.ticket import Ticket


class TicketMessage(CreatedAtMixin, Base):
    """La conversación del ticket después del primer mensaje (que está en tickets.text).

    Se ordena por (created_at, id): created_at es la hora de inicio de la
    transacción, así que la respuesta del cliente y la plantilla que la sigue
    comparten created_at; el uuid7 desempata en el orden en que se crearon.
    """

    __tablename__ = "ticket_messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE")
    )
    author: Mapped[MessageAuthor] = mapped_column(str_enum(MessageAuthor, "author"))
    # SET NULL: si se borra al agente, sus mensajes se conservan.
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[MessageStatus] = mapped_column(
        str_enum(MessageStatus, "message_status"),
        default=MessageStatus.SENT,
        server_default=MessageStatus.SENT.value,
    )
    # Copia de lo citado (sección, versión, fragmento), no una FK: reindexar el
    # manual o cambiar de versión no debe romper el historial.
    sources: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # Solo en respuestas de la IA (v2): veredicto del juez y con qué se generó.
    judge_passed: Mapped[bool | None] = mapped_column(Boolean)
    judge_reason: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(50))

    ticket: Mapped[Ticket] = relationship(back_populates="messages", lazy="raise")

    __table_args__ = (
        CheckConstraint("length(trim(content)) > 0", name="content_not_empty"),
        # Lo que escribe el cliente, con el mismo límite que el primer mensaje.
        # Las respuestas del sistema, la IA y los agentes no lo necesitan.
        CheckConstraint(
            f"author <> 'customer' OR length(content) <= {TICKET_TEXT_MAX_LENGTH}",
            name="customer_content_max_length",
        ),
        # La IA, el sistema y el cliente nunca tienen agente. Al revés no se
        # puede exigir: con SET NULL, el mensaje de un agente borrado lo incumpliría.
        CheckConstraint(
            "author = 'agent' OR agent_id IS NULL", name="agent_only_for_agent"
        ),
        # Solo la IA deja borradores (los que rechaza el juez).
        CheckConstraint("author = 'ai' OR status = 'sent'", name="draft_only_for_ai"),
        Index("ix_ticket_messages_ticket_id_created_at", "ticket_id", "created_at"),
    )
