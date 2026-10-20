import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from safe_reply.models import TicketCorrection


class TicketCorrectionRepository:
    """Acceso a ticket_corrections. No hace commit()."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, correction: TicketCorrection) -> TicketCorrection:
        self.session.add(correction)
        await self.session.flush()
        return correction

    async def list_for_ticket(self, ticket_id: uuid.UUID) -> Sequence[TicketCorrection]:
        stmt = (
            select(TicketCorrection)
            .where(TicketCorrection.ticket_id == ticket_id)
            .order_by(TicketCorrection.created_at, TicketCorrection.id)
        )
        return (await self.session.scalars(stmt)).all()
