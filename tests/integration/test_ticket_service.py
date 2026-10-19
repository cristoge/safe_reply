"""TicketService contra Postgres y Redis reales: lo que no se ve bien por HTTP."""

from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.core.exceptions import IdempotencyKeyReusedError
from revision_rag.models import Ticket, User
from revision_rag.repositories.ticket import TicketRepository
from revision_rag.repositories.ticket_event import TicketEventRepository
from revision_rag.repositories.ticket_message import TicketMessageRepository
from revision_rag.schemas.ticket import TicketCreate
from revision_rag.services.rate_limiter import RateLimiter
from revision_rag.services.ticket import TicketService

TEXT = "My card hasn't arrived yet"


@pytest.fixture
def service(db_session: AsyncSession, redis: Redis) -> TicketService:
    return TicketService(
        db_session,
        TicketRepository(db_session),
        TicketMessageRepository(db_session),
        TicketEventRepository(db_session),
        RateLimiter(redis, limit=5, window_seconds=60),
    )


@pytest.fixture
async def customer(db_session: AsyncSession) -> User:
    user = User(email="cristo@test.com", hashed_password="x", phone="+34600123456")
    db_session.add(user)
    await db_session.commit()
    return user


async def lose_the_race(
    service: TicketService, db_session: AsyncSession, customer: User
) -> None:
    """Simula dos peticiones simultáneas con la misma key.

    La otra petición crea el ticket justo después de que esta haya hecho el
    SELECT (que, por tanto, no lo ha visto). Esta llega al INSERT y choca con
    el índice único.
    """
    db_session.add(Ticket(customer_id=customer.id, text=TEXT, idempotency_key="k"))
    await db_session.commit()

    real_lookup = service.tickets.get_by_idempotency_key
    calls = 0

    async def lookup_after_the_other_request(*args: Any) -> Ticket | None:
        nonlocal calls
        calls += 1
        return None if calls == 1 else await real_lookup(*args)

    service.tickets.get_by_idempotency_key = lookup_after_the_other_request  # type: ignore[method-assign]


async def test_losing_the_race_returns_the_winners_ticket(
    service: TicketService, db_session: AsyncSession, customer: User
) -> None:
    await lose_the_race(service, db_session, customer)

    ticket = await service.create(customer, TicketCreate(text=TEXT), "k")

    assert ticket.idempotency_key == "k"
    assert await db_session.scalar(select(func.count()).select_from(Ticket)) == 1


async def test_losing_the_race_with_other_text_raises_422(
    service: TicketService, db_session: AsyncSession, customer: User
) -> None:
    await lose_the_race(service, db_session, customer)

    with pytest.raises(IdempotencyKeyReusedError):
        await service.create(customer, TicketCreate(text="Something else"), "k")
