"""Dos agentes reclamando a la vez nunca se llevan el mismo ticket.

Con conexiones reales y separadas, no con db_session: dentro de una sola
transacción no hay nadie con quien competir. Por eso aquí los datos se
confirman de verdad y se borran al terminar.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from safe_reply.models import Ticket, User
from safe_reply.models.enums import TicketStatus, UserRole
from safe_reply.repositories.ticket import TicketRepository
from safe_reply.repositories.ticket_correction import TicketCorrectionRepository
from safe_reply.repositories.ticket_event import TicketEventRepository
from safe_reply.repositories.ticket_message import TicketMessageRepository
from safe_reply.repositories.user import UserRepository
from safe_reply.services.human_queue import HumanQueueService
from safe_reply.triage.rules import Rules


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def setup(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[list[User], list[uuid.UUID]]]:
    """Dos agentes y dos tickets en la cola, confirmados (y borrados al final)."""
    suffix = uuid.uuid4().hex[:8]
    customer = User(
        email=f"cliente-{suffix}@test.com", hashed_password="x", phone="+34600123456"
    )
    agents = [
        User(
            email=f"agente{i}-{suffix}@test.com",
            hashed_password="x",
            role=UserRole.AGENT,
        )
        for i in range(2)
    ]
    async with sessions() as session:
        session.add_all([customer, *agents])
        await session.flush()
        tickets = [
            Ticket(
                customer_id=customer.id,
                text=f"ticket {i}",
                status=TicketStatus.IN_HUMAN_QUEUE,
                priority=2,
                due_at=datetime.now(UTC) + timedelta(hours=i),
            )
            for i in range(2)
        ]
        session.add_all(tickets)
        await session.commit()
    try:
        yield agents, [t.id for t in tickets]
    finally:
        async with sessions() as session:
            # CASCADE se lleva los tickets y sus eventos.
            ids = [customer.id, *(a.id for a in agents)]
            await session.execute(delete(User).where(User.id.in_(ids)))
            await session.commit()


async def test_skip_locked_gives_a_second_agent_the_next_ticket(
    sessions: async_sessionmaker[AsyncSession],
    setup: tuple[list[User], list[uuid.UUID]],
) -> None:
    _, ticket_ids = setup
    stale_before = datetime.now(UTC) - timedelta(hours=2)
    async with sessions() as first, sessions() as second:
        # El primero reclama y no confirma: su fila sigue bloqueada.
        taken = await TicketRepository(first).claim_next(stale_before=stale_before)
        # El segundo no espera ni coge el mismo: se lo salta y coge el siguiente.
        other = await TicketRepository(second).claim_next(stale_before=stale_before)
        assert taken is not None and other is not None
        assert [taken.id, other.id] == ticket_ids
        await first.rollback()
        await second.rollback()


def make_service(session: AsyncSession, rules: Rules) -> HumanQueueService:
    return HumanQueueService(
        session,
        TicketRepository(session),
        TicketMessageRepository(session),
        TicketEventRepository(session),
        TicketCorrectionRepository(session),
        UserRepository(session),
        rules,
    )


async def test_two_agents_claiming_at_once_get_different_tickets(
    sessions: async_sessionmaker[AsyncSession],
    setup: tuple[list[User], list[uuid.UUID]],
    rules: Rules,
) -> None:
    agents, ticket_ids = setup

    async def claim(agent: User) -> uuid.UUID | None:
        async with sessions() as session:
            ticket = await make_service(session, rules).claim_next(agent)
            return ticket.id if ticket is not None else None

    claimed = await asyncio.gather(*(claim(a) for a in agents))

    # Dos resultados y los dos tickets: ninguno repetido, ninguno sin ticket.
    assert set(claimed) == set(ticket_ids)
