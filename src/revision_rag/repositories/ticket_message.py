import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.models import TicketMessage
from revision_rag.models.enums import MessageStatus


class TicketMessageRepository:
    """Acceso a la tabla ticket_messages. No hace commit()."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, message: TicketMessage) -> TicketMessage:
        self.session.add(message)
        await self.session.flush()
        return message

    async def list_sent(self, ticket_id: uuid.UUID) -> Sequence[TicketMessage]:
        """Los mensajes que ve el cliente, en orden. Los borradores, nunca.

        created_at no basta para ordenar: los mensajes de una misma transacción
        lo comparten. El uuid7 desempata en el orden en que se crearon.
        """
        stmt = (
            select(TicketMessage)
            .where(
                TicketMessage.ticket_id == ticket_id,
                TicketMessage.status == MessageStatus.SENT,
            )
            .order_by(TicketMessage.created_at, TicketMessage.id)
        )
        return (await self.session.scalars(stmt)).all()
