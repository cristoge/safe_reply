import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from safe_reply.core.exceptions import (
    IdempotencyKeyReusedError,
    PhoneRequiredError,
    TicketNotFoundError,
    TicketStateConflictError,
)
from safe_reply.models import Ticket, TicketEvent, TicketMessage, User
from safe_reply.models.enums import MessageAuthor, TicketEventType, TicketStatus
from safe_reply.repositories.ticket import TicketRepository
from safe_reply.repositories.ticket_event import TicketEventRepository
from safe_reply.repositories.ticket_message import TicketMessageRepository
from safe_reply.schemas.ticket import (
    MessageCreate,
    MessageRead,
    TicketCreate,
    TicketDetail,
)
from safe_reply.services.rate_limiter import RateLimiter
from safe_reply.tasks.queue import TaskQueue
from safe_reply.triage.answers import CALL_TEMPLATE
from safe_reply.triage.rules import Rules
from safe_reply.triage.urgency import due_at, escalated_priority

logger = logging.getLogger(__name__)

CUSTOMER_ESCALATED = "customer escalated"


class TicketService:
    """Lo que hace el cliente con sus tickets: abrirlos, consultarlos, contestar y escalar.

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
        rules: Rules,
    ) -> None:
        self.session = session
        self.tickets = tickets
        self.messages = messages
        self.events = events
        self.rate_limiter = rate_limiter
        self.queue = queue
        self.rules = rules

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
        logger.info(
            "ticket_created",
            extra={"ticket_id": ticket.id, "customer_id": customer_id},
        )

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

    async def escalate(self, ticket: Ticket) -> Ticket:
        """ "I still need help": de answered a la cola humana.

        Si ya está con una persona no hace nada (pulsar dos veces, un
        reintento): la salida a una persona ya está garantizada.
        """
        ticket = await self._lock(ticket.id)
        if ticket.status in (TicketStatus.IN_HUMAN_QUEUE, TicketStatus.CONTACTED):
            return ticket
        self._check_can_write(ticket)
        await self._escalate(ticket)
        await self.session.commit()
        self._log_escalated(ticket, by="button")
        return ticket

    async def reply(self, ticket: Ticket, data: MessageCreate) -> Ticket:
        """El cliente contesta (apartado 6 › Cuando el cliente contesta).

        Tras una respuesta (answered) es escalar, con su texto. Si ya está con
        una persona, se adjunta para el agente sin cambiar estado, prioridad ni
        plazo. No se vuelve a clasificar: fuera de contexto ("it still hasn't
        arrived") el clasificador falla, y el camino a persona no pasa por
        ningún modelo.
        """
        ticket = await self._lock(ticket.id)
        self._check_can_write(ticket)
        await self.messages.add(
            TicketMessage(
                ticket_id=ticket.id, author=MessageAuthor.CUSTOMER, content=data.text
            )
        )
        escalating = ticket.status == TicketStatus.ANSWERED
        if escalating:
            await self._escalate(ticket)
        else:
            await self.events.add(
                TicketEvent(
                    ticket_id=ticket.id,
                    event=TicketEventType.CUSTOMER_REPLIED,
                    actor_id=ticket.customer_id,
                )
            )
        await self.session.commit()
        if escalating:
            self._log_escalated(ticket, by="reply")
        else:
            logger.info(
                "customer_replied",
                extra={"ticket_id": ticket.id, "status": ticket.status},
            )
        return ticket

    @staticmethod
    def _log_escalated(ticket: Ticket, *, by: str) -> None:
        # route_reason dice qué respuesta no sirvió: con esto se calcula el %
        # de escalados por intención (apartado 6 › Escalar).
        logger.info(
            "ticket_escalated",
            extra={
                "ticket_id": ticket.id,
                "by": by,
                "intent": ticket.intent,
                "route_reason": ticket.route_reason,
                "priority": ticket.priority,
            },
        )

    async def _lock(self, ticket_id: uuid.UUID) -> Ticket:
        # El dueño ya lo ha comprobado OwnedTicket; aquí se vuelve a leer con
        # la fila bloqueada para decidir sobre el estado actual: escalar y
        # contestar a la vez no pueden escalar dos veces.
        ticket = await self.tickets.get_for_update(ticket_id)
        if ticket is None:
            raise TicketNotFoundError
        return ticket

    @staticmethod
    def _check_can_write(ticket: Ticket) -> None:
        match ticket.status:
            case TicketStatus.RESOLVED:
                # Cada ticket es un problema: reabrirlo obligaría a decidir de
                # nuevo prioridad y plazo.
                raise TicketStateConflictError(
                    "El ticket está resuelto: si necesitas algo más, abre uno nuevo"
                )
            case TicketStatus.RECEIVED | TicketStatus.PROCESSING:
                raise TicketStateConflictError(
                    "El ticket aún se está procesando; inténtalo en unos segundos"
                )
            case _:
                pass

    async def _escalate(self, ticket: Ticket) -> None:
        """answered → in_human_queue: un nivel más de prioridad y plazo nuevo.

        route y route_reason no cambian: cuentan lo que decidió el sistema
        (para las métricas de escalados y para las correcciones); escalated_at
        dice que el cliente no quedó satisfecho.
        """
        assert ticket.priority is not None  # un ticket answered ya está procesado
        now = datetime.now(UTC)
        priority = escalated_priority(ticket.priority, ticket.priority_reason)
        ticket.status = TicketStatus.IN_HUMAN_QUEUE
        ticket.priority = priority.level
        ticket.priority_reason = priority.reason
        ticket.escalated_at = now
        # Desde que escala, no desde que escribió: con el plazo original, un
        # ticket respondido hace dos días saltaría delante de toda la cola.
        ticket.due_at = due_at(now, priority.level, self.rules)
        await self.events.add(
            TicketEvent(
                ticket_id=ticket.id,
                event=TicketEventType.ESCALATED,
                actor_id=ticket.customer_id,
            )
        )
        await self.events.add(
            TicketEvent(
                ticket_id=ticket.id,
                event=TicketEventType.STATUS_CHANGED,
                from_status=TicketStatus.ANSWERED,
                to_status=TicketStatus.IN_HUMAN_QUEUE,
                actor_id=ticket.customer_id,
                reason=CUSTOMER_ESCALATED,
            )
        )
        await self.messages.add(
            TicketMessage(
                ticket_id=ticket.id, author=MessageAuthor.SYSTEM, content=CALL_TEMPLATE
            )
        )
