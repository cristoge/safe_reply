import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from revision_rag.db.base import Base, CreatedAtMixin
from revision_rag.models.enums import Route, str_enum

if TYPE_CHECKING:
    from revision_rag.models.ticket import Ticket


class TicketCorrection(CreatedAtMixin, Base):
    """Una revisión de un agente: lo que había decidido el sistema y lo que corrige.

    Los predicted_* copian el ticket en el momento de corregir: si después se
    reprocesa con otro modelo, la corrección sigue diciendo qué se corrigió.
    Un corrected_* null = de acuerdo. Los pares (tickets.text, corrected_intent)
    son los datos para reentrenar.
    """

    __tablename__ = "ticket_corrections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Los predicted_* también pueden ser null: un ticket que falló al procesarse
    # ("processing error") no llegó a clasificarse, y su corrección es justo la
    # etiqueta que falta.
    predicted_intent: Mapped[str | None] = mapped_column(String(100))
    corrected_intent: Mapped[str | None] = mapped_column(String(100))
    predicted_priority: Mapped[int | None] = mapped_column(SmallInteger)
    corrected_priority: Mapped[int | None] = mapped_column(SmallInteger)
    # Dos columnas con el mismo enum: cada una necesita su nombre, o los dos
    # CHECK se llamarían igual.
    predicted_route: Mapped[Route | None] = mapped_column(
        str_enum(Route, "predicted_route")
    )
    corrected_route: Mapped[Route | None] = mapped_column(
        str_enum(Route, "corrected_route")
    )
    note: Mapped[str | None] = mapped_column(Text)

    ticket: Mapped[Ticket] = relationship(back_populates="corrections", lazy="raise")

    __table_args__ = (
        CheckConstraint(
            "predicted_priority BETWEEN 1 AND 3", name="predicted_priority_range"
        ),
        CheckConstraint(
            "corrected_priority BETWEEN 1 AND 3", name="corrected_priority_range"
        ),
    )
