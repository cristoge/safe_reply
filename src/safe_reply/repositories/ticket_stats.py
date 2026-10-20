from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from safe_reply.models import Ticket
from safe_reply.models.enums import Route, TicketStatus


@dataclass(frozen=True)
class QueueSummary:
    waiting: int  # in_human_queue: nadie ha hablado aún con el cliente
    unassigned: int
    overdue: int  # con el plazo (due_at) ya pasado
    oldest_created_at: datetime | None


@dataclass(frozen=True)
class IntentEscalations:
    intent: str
    answered: int  # respondidos por la IA (route = ai)
    escalated: int


class TicketStatsRepository:
    """Cifras agregadas de tickets para las alertas. Solo lectura."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def route_counts(self, *, since: datetime) -> dict[Route, int]:
        """Tickets procesados desde `since`, por ruta (sin los aún sin procesar)."""
        stmt = (
            select(Ticket.route, func.count())
            .where(Ticket.created_at >= since, Ticket.route.is_not(None))
            .group_by(Ticket.route)
        )
        rows = await self.session.execute(stmt)
        return {route: count for route, count in rows if route is not None}

    async def queue_summary(self, *, now: datetime) -> QueueSummary:
        stmt = select(
            func.count(),
            func.count().filter(Ticket.assigned_agent_id.is_(None)),
            func.count().filter(Ticket.due_at < now),
            func.min(Ticket.created_at),
        ).where(Ticket.status == TicketStatus.IN_HUMAN_QUEUE)
        waiting, unassigned, overdue, oldest = (await self.session.execute(stmt)).one()
        return QueueSummary(waiting, unassigned, overdue, oldest)

    async def count_stuck(self, *, before: datetime) -> int:
        """En received/processing desde antes de `before`: procesado a medias."""
        stmt = select(func.count()).where(
            Ticket.status.in_([TicketStatus.RECEIVED, TicketStatus.PROCESSING]),
            Ticket.created_at < before,
        )
        return await self.session.scalar(stmt) or 0

    async def escalations_by_intent(
        self, *, since: datetime
    ) -> list[IntentEscalations]:
        # route no cambia al escalar (S5): sigue diciendo que respondió la IA, y
        # escalated_at dice que la respuesta no sirvió.
        stmt = (
            select(
                Ticket.intent,
                func.count(),
                func.count(Ticket.escalated_at),
            )
            .where(Ticket.route == Route.AI, Ticket.created_at >= since)
            .group_by(Ticket.intent)
            .order_by(Ticket.intent)
        )
        rows = await self.session.execute(stmt)
        return [
            IntentEscalations(intent, answered, escalated)
            for intent, answered, escalated in rows
            if intent is not None
        ]
