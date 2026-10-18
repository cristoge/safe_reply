import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from revision_rag.db.base import Base, TimestampMixin
from revision_rag.models.enums import Route, TicketStatus, str_enum

if TYPE_CHECKING:
    from revision_rag.models.ticket_correction import TicketCorrection
    from revision_rag.models.ticket_event import TicketEvent
    from revision_rag.models.ticket_message import TicketMessage
    from revision_rag.models.user import User

# También lo comprueba Pydantic; el CHECK protege de cualquier otro camino
# (scripts, un bug). Un texto enorme cuesta tokens en el LLM y es una vía de DoS.
TICKET_TEXT_MAX_LENGTH = 2000


class Ticket(TimestampMixin, Base):
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    # El primer mensaje del cliente. No cambia nunca: es lo que se clasifica y,
    # con la corrección del agente, lo que sirve para reentrenar. Las respuestas
    # posteriores van a ticket_messages.
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[TicketStatus] = mapped_column(
        str_enum(TicketStatus, "ticket_status"),
        default=TicketStatus.RECEIVED,
        server_default=TicketStatus.RECEIVED.value,
    )

    # Resultado del procesado: null hasta entonces. intent y ticket_type sin
    # CHECK: la lista válida es rules/intents.yaml y cambiarla no debe exigir
    # una migración. Se guarda lo que se decidió, no la regla: si el YAML cambia,
    # los tickets antiguos siguen explicando su decisión.
    intent: Mapped[str | None] = mapped_column(String(100))
    confidence: Mapped[float | None] = mapped_column(Float)
    ticket_type: Mapped[str | None] = mapped_column(String(50))
    # 1 = low · 2 = medium · 3 = high. Número y no texto para poder comparar y
    # sumar (escalar = +1); como texto, el orden sería high < low < medium.
    priority: Mapped[int | None] = mapped_column(SmallInteger)
    priority_reason: Mapped[str | None] = mapped_column(Text)
    route: Mapped[Route | None] = mapped_column(str_enum(Route, "route"))
    # Texto libre: hay más de 8 motivos y crecerán con el RAG.
    route_reason: Mapped[str | None] = mapped_column(Text)
    classifier_version: Mapped[str | None] = mapped_column(String(50))

    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # SET NULL: si se borra al agente, el ticket y su historial se conservan.
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # Para detectar tickets reclamados hace horas y nunca atendidos.
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Plazo para atenderlo según la prioridad (los plazos están en el YAML). La
    # cola se ordena por esto y no por prioridad: así un ticket low antiguo
    # acaba pasando delante de los high nuevos (sin inanición).
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[User] = relationship(
        back_populates="tickets", foreign_keys=[customer_id], lazy="raise"
    )
    messages: Mapped[list[TicketMessage]] = relationship(
        back_populates="ticket", lazy="raise", passive_deletes=True
    )
    corrections: Mapped[list[TicketCorrection]] = relationship(
        back_populates="ticket", lazy="raise", passive_deletes=True
    )
    events: Mapped[list[TicketEvent]] = relationship(
        back_populates="ticket", lazy="raise", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("length(trim(text)) > 0", name="text_not_empty"),
        CheckConstraint(
            f"length(text) <= {TICKET_TEXT_MAX_LENGTH}", name="text_max_length"
        ),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
        CheckConstraint("priority BETWEEN 1 AND 3", name="priority_range"),
        # "Mis tickets" del cliente, los más recientes primero. También sirve
        # como índice de customer_id para el CASCADE.
        Index("ix_tickets_customer_id_created_at", "customer_id", "created_at"),
        # La cola humana: WHERE status = 'in_human_queue' ORDER BY due_at.
        Index("ix_tickets_status_due_at", "status", "due_at"),
    )
