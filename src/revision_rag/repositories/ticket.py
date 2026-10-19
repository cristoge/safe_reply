import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.models import Ticket
from revision_rag.models.enums import TicketStatus


class TicketRepository:
    """Acceso a la tabla tickets. No hace commit(): eso lo decide el servicio.

    Las consultas del cliente filtran siempre por customer_id: no hay forma de
    pedir el ticket de otro.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, ticket: Ticket) -> Ticket:
        self.session.add(ticket)
        await self.session.flush()
        return ticket

    async def get_for_customer(
        self, ticket_id: uuid.UUID, customer_id: uuid.UUID
    ) -> Ticket | None:
        # Si el ticket existe pero es de otro, da None igual que si no
        # existiera: desde fuera no se puede distinguir (404, no 403).
        stmt = select(Ticket).where(
            Ticket.id == ticket_id, Ticket.customer_id == customer_id
        )
        return await self.session.scalar(stmt)

    async def get(self, ticket_id: uuid.UUID) -> Ticket | None:
        """Sin filtro de dueño: solo para el sistema (el procesado), nunca para rutas."""
        return await self.session.get(Ticket, ticket_id)

    async def start_processing(self, ticket_id: uuid.UUID) -> Ticket | None:
        """received → processing, solo si sigue en received. None si no.

        Transición condicional en un solo UPDATE: si dos procesados cogen el
        mismo ticket (con ARQ, un trabajo puede llegar dos veces), solo uno
        actualiza la fila; el otro recibe None y no hace nada.
        """
        stmt = (
            update(Ticket)
            .where(Ticket.id == ticket_id, Ticket.status == TicketStatus.RECEIVED)
            .values(status=TicketStatus.PROCESSING)
            .returning(Ticket)
        )
        return await self.session.scalar(stmt)

    async def count_recent_for_customer(
        self, customer_id: uuid.UUID, since: datetime
    ) -> int:
        # Usa el índice (customer_id, created_at).
        stmt = select(func.count()).where(
            Ticket.customer_id == customer_id, Ticket.created_at >= since
        )
        return await self.session.scalar(stmt) or 0

    async def get_by_idempotency_key(
        self, customer_id: uuid.UUID, idempotency_key: str
    ) -> Ticket | None:
        # Usa el índice único parcial (customer_id, idempotency_key).
        stmt = select(Ticket).where(
            Ticket.customer_id == customer_id,
            Ticket.idempotency_key == idempotency_key,
        )
        return await self.session.scalar(stmt)

    async def list_for_customer(
        self, customer_id: uuid.UUID, *, limit: int, offset: int
    ) -> Sequence[Ticket]:
        # Los más nuevos primero; el uuid7 desempata para que el orden sea
        # estable entre páginas. Usa el índice (customer_id, created_at).
        stmt = (
            select(Ticket)
            .where(Ticket.customer_id == customer_id)
            .order_by(Ticket.created_at.desc(), Ticket.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.scalars(stmt)).all()
