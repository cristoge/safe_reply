import uuid
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.core.exceptions import (
    IdempotencyKeyReusedError,
    PhoneRequiredError,
    TicketNotFoundError,
)
from revision_rag.models import Ticket, TicketEvent, User
from revision_rag.models.enums import MessageAuthor, TicketEventType
from revision_rag.repositories.ticket import TicketRepository
from revision_rag.repositories.ticket_event import TicketEventRepository
from revision_rag.repositories.ticket_message import TicketMessageRepository
from revision_rag.schemas.ticket import MessageRead, TicketCreate, TicketDetail
from revision_rag.services.rate_limiter import RateLimiter
from revision_rag.tasks.queue import TaskQueue


class TicketService:
    """Lo que hace el cliente con sus tickets: abrirlos y consultarlos.

    Un ticket nuevo se guarda en received y se encola para procesarlo (en la v1,
    InlineTaskQueue lo procesa al momento, dentro de la misma petición).
    """

    def __init__(
        self,
        session: AsyncSession,
        tickets: TicketRepository,
        messages: TicketMessageRepository,
        events: TicketEventRepository,
        rate_limiter: RateLimiter,
        queue: TaskQueue,
    ) -> None:
        self.session = session
        self.tickets = tickets
        self.messages = messages
        self.events = events
        self.rate_limiter = rate_limiter
        self.queue = queue

    async def create(
        self, customer: User, data: TicketCreate, idempotency_key: str | None = None
    ) -> Ticket:
        """Crea el ticket en received con su evento created y lo encola para procesarlo.

        Con Idempotency-Key, un reintento devuelve el ticket que ya se creó con
        ella, sin crear otro ni gastar cupo del rate limit (no es una acción
        nueva del cliente). La misma key con otro texto → 422.
        """
        if customer.phone is None:
            raise PhoneRequiredError
        # Se guarda antes: un rollback caduca los objetos de la sesión y leer
        # customer.id después intentaría recargarlo (en async, MissingGreenlet).
        customer_id = customer.id

        if idempotency_key is not None:
            existing = await self.tickets.get_by_idempotency_key(
                customer_id, idempotency_key
            )
            if existing is not None:
                return self._replay(existing, data)

        await self.rate_limiter.hit(f"tickets:{customer_id}")

        ticket = Ticket(
            customer_id=customer_id, text=data.text, idempotency_key=idempotency_key
        )
        try:
            await self.tickets.add(ticket)
        except IntegrityError as exc:
            # Dos peticiones simultáneas con la misma key: las dos pasan el
            # SELECT de arriba, pero el índice único solo deja insertar a una.
            # Postgres hace esperar al segundo INSERT hasta que la primera
            # transacción confirma, así que aquí el ticket ganador ya es visible.
            if "uq_tickets_customer_id_idempotency_key" not in str(exc.orig):
                raise
            await self.session.rollback()
            assert idempotency_key is not None  # sin key no hay índice que chocar
            winner = await self.tickets.get_by_idempotency_key(
                customer_id, idempotency_key
            )
            if winner is None:  # solo si lo han borrado entre medias
                raise
            return self._replay(winner, data)

        await self.events.add(
            TicketEvent(
                ticket_id=ticket.id, event=TicketEventType.CREATED, actor_id=customer_id
            )
        )
        # Ticket (con su key) y evento en un solo commit: nunca uno sin el otro.
        await self.session.commit()

        # Después del commit: el ticket ya existe pase lo que pase con el
        # procesado (y si este falla, el ticket acaba en la cola humana).
        await self.queue.enqueue_ticket(ticket.id)
        # Lo que haya decidido el procesado (con ARQ, seguiría en received).
        await self.session.refresh(ticket)
        return ticket

    @staticmethod
    def _replay(existing: Ticket, data: TicketCreate) -> Ticket:
        # tickets.text no cambia nunca: basta compararlo para saber si el
        # reintento trae el mismo contenido (no hace falta guardar una huella).
        if existing.text != data.text:
            raise IdempotencyKeyReusedError
        return existing

    async def get(self, customer: User, ticket_id: uuid.UUID) -> Ticket:
        ticket = await self.tickets.get_for_customer(ticket_id, customer.id)
        if ticket is None:
            raise TicketNotFoundError
        return ticket

    async def list_tickets(
        self, customer: User, *, limit: int, offset: int
    ) -> Sequence[Ticket]:
        return await self.tickets.list_for_customer(
            customer.id, limit=limit, offset=offset
        )

    async def detail(self, ticket: Ticket) -> TicketDetail:
        """El ticket con su conversación: tickets.text y después los mensajes enviados."""
        first = MessageRead(
            author=MessageAuthor.CUSTOMER,
            content=ticket.text,
            created_at=ticket.created_at,
        )
        sent = await self.messages.list_sent(ticket.id)
        return TicketDetail(
            id=ticket.id,
            status=ticket.status,
            ticket_type=ticket.ticket_type,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
            messages=[first, *(MessageRead.model_validate(m) for m in sent)],
        )
