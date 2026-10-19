import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.models import Ticket


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
