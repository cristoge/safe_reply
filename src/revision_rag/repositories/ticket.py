import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, or_, select, update
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

    async def get_for_update(self, ticket_id: uuid.UUID) -> Ticket | None:
        """El ticket con la fila bloqueada hasta el commit (sin filtro de dueño).

        Para cambiar su estado: dos peticiones a la vez sobre el mismo ticket
        (escalar y contestar, dos agentes...) esperan su turno y la segunda ve
        lo que dejó la primera. populate_existing: si ya estaba en la sesión,
        se refresca con lo que hay en la BD en vez de devolver la copia vieja.
        """
        stmt = (
            select(Ticket)
            .where(Ticket.id == ticket_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(stmt)

    async def claim_next(self, *, stale_before: datetime) -> Ticket | None:
        """El siguiente ticket de la cola humana, bloqueado para que nadie más lo coja.

        Sin asignar, o asignado antes de stale_before y sin contactar (sigue en
        in_human_queue): un reclamado y abandonado vuelve a estar disponible en
        la propia consulta, sin un barrido que lo libere.

        SKIP LOCKED: si otro agente está reclamando esa fila, se la salta y
        coge la siguiente, sin esperar y sin que dos agentes se lleven el mismo
        (el cliente recibiría dos llamadas del banco). Por plazo, no por
        prioridad: sin inanición. Usa el índice (status, due_at).
        """
        stmt = (
            select(Ticket)
            .where(
                Ticket.status == TicketStatus.IN_HUMAN_QUEUE,
                or_(
                    Ticket.assigned_agent_id.is_(None),
                    Ticket.assigned_at < stale_before,
                ),
            )
            .order_by(Ticket.due_at, Ticket.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(stmt)

    async def list_queue(self, *, limit: int, offset: int) -> Sequence[Ticket]:
        """La cola humana entera (reclamados o no), en el orden en que se atiende."""
        stmt = (
            select(Ticket)
            .where(Ticket.status == TicketStatus.IN_HUMAN_QUEUE)
            .order_by(Ticket.due_at, Ticket.id)
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.scalars(stmt)).all()

    async def list_open_for_agent(
        self, agent_id: uuid.UUID, *, limit: int, offset: int
    ) -> Sequence[Ticket]:
        """Los tickets que el agente tiene entre manos (reclamados y sin resolver)."""
        stmt = (
            select(Ticket)
            .where(
                Ticket.assigned_agent_id == agent_id,
                Ticket.status.in_(
                    [TicketStatus.IN_HUMAN_QUEUE, TicketStatus.CONTACTED]
                ),
            )
            .order_by(Ticket.due_at, Ticket.id)
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.scalars(stmt)).all()

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
