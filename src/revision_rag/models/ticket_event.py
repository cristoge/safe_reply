import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from revision_rag.db.base import Base, CreatedAtMixin
from revision_rag.models.enums import TicketEventType, TicketStatus, str_enum

if TYPE_CHECKING:
    from revision_rag.models.ticket import Ticket


class TicketEvent(CreatedAtMixin, Base):
    """Historial del ticket: cada cambio de estado, asignación, llamada fallida y escalado.

    Solo se añaden filas, nunca se modifican ni se borran (auditoría). Se escribe
    en la misma transacción que el cambio en tickets: nunca un cambio sin su
    evento. tickets guarda el estado actual; esto, cómo llegó hasta ahí.
    """

    __tablename__ = "ticket_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE")
    )
    event: Mapped[TicketEventType] = mapped_column(str_enum(TicketEventType, "event"))
    from_status: Mapped[TicketStatus | None] = mapped_column(
        str_enum(TicketStatus, "from_status")
    )
    to_status: Mapped[TicketStatus | None] = mapped_column(
        str_enum(TicketStatus, "to_status")
    )
    # null = lo hizo el sistema (el procesado, el barrido). SET NULL: si se
    # borra al agente, el historial se conserva.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # "no answer", "processing timeout", "claimed > 2 h without contact"...
    reason: Mapped[str | None] = mapped_column(Text)

    ticket: Mapped[Ticket] = relationship(back_populates="events", lazy="raise")

    __table_args__ = (
        # Los dos estados van juntos y solo en status_changed: así cada
        # transición se lee con una consulta, sin interpretar otros eventos.
        CheckConstraint(
            "(event = 'status_changed'"
            " AND from_status IS NOT NULL AND to_status IS NOT NULL)"
            " OR (event <> 'status_changed'"
            " AND from_status IS NULL AND to_status IS NULL)",
            name="statuses_only_on_status_changed",
        ),
        Index("ix_ticket_events_ticket_id_created_at", "ticket_id", "created_at"),
    )
