import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from safe_reply.models import TicketEvent
from safe_reply.models.enums import TicketEventType


class TicketEventRepository:
    """Acceso a ticket_events. Solo añade: el historial no se modifica ni se borra."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, event: TicketEvent) -> TicketEvent:
        # Sin commit: el evento se confirma en la misma transacción que el
        # cambio del ticket, así nunca hay un cambio sin su evento.
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_for_ticket(self, ticket_id: uuid.UUID) -> Sequence[TicketEvent]:
        # Los de una misma transacción comparten created_at: el uuid7 desempata.
        stmt = (
            select(TicketEvent)
            .where(TicketEvent.ticket_id == ticket_id)
            .order_by(TicketEvent.created_at, TicketEvent.id)
        )
        return (await self.session.scalars(stmt)).all()

    async def count_failed_calls(
        self, ticket_id: uuid.UUID, *, reset_reason: str
    ) -> int:
        """Las llamadas fallidas desde el último unassigned con reset_reason.

        Tras el aviso "we tried to call you…" la cuenta empieza de cero; si no,
        la siguiente llamada fallida volvería a mandarlo. Otros unassigned (un
        reclamado y abandonado) no la reinician: el agente nuevo sigue la
        cuenta del anterior. Se compara por id y no por created_at: la última
        llamada fallida y su unassigned van en la misma transacción (mismo
        created_at), y el uuid7 dice cuál fue antes.
        """
        last_reset = (
            select(TicketEvent.id)
            .where(
                TicketEvent.ticket_id == ticket_id,
                TicketEvent.event == TicketEventType.UNASSIGNED,
                TicketEvent.reason == reset_reason,
            )
            .order_by(TicketEvent.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        stmt = select(func.count()).where(
            TicketEvent.ticket_id == ticket_id,
            TicketEvent.event == TicketEventType.CALL_FAILED,
            or_(last_reset.is_(None), TicketEvent.id > last_reset),
        )
        return await self.session.scalar(stmt) or 0
