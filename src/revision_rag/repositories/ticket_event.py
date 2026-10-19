from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.models import TicketEvent


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
