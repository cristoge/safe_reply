"""TicketProcessor contra Postgres real: cada camino de un ticket y su rastro en la BD."""

from datetime import timedelta

import pytest
from fakes import FakeClassifier
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.models import Ticket, TicketEvent, TicketMessage, User
from revision_rag.models.enums import (
    MessageAuthor,
    Route,
    TicketEventType,
    TicketStatus,
)
from revision_rag.repositories.ticket import TicketRepository
from revision_rag.repositories.ticket_event import TicketEventRepository
from revision_rag.repositories.ticket_message import TicketMessageRepository
from revision_rag.services.ticket_processor import (
    CANNED_RAG_DISABLED,
    PROCESSING_ERROR,
    TicketProcessor,
)
from revision_rag.triage import router
from revision_rag.triage.answers import CALL_TEMPLATE, PROVISIONAL_ANSWER, CannedAnswers
from revision_rag.triage.rules import Rules

AGE_LIMIT_ANSWER = "You can open an account from the age of 18."


def make_processor(
    db_session: AsyncSession, classifier: FakeClassifier, rules: Rules
) -> TicketProcessor:
    return TicketProcessor(
        db_session,
        TicketRepository(db_session),
        TicketMessageRepository(db_session),
        TicketEventRepository(db_session),
        classifier,
        rules,
        CannedAnswers({"age_limit": AGE_LIMIT_ANSWER}),
    )


@pytest.fixture
def processor(
    db_session: AsyncSession, classifier: FakeClassifier, rules: Rules
) -> TicketProcessor:
    return make_processor(db_session, classifier, rules)


@pytest.fixture
async def customer(db_session: AsyncSession) -> User:
    user = User(email="cristo@test.com", hashed_password="x", phone="+34600123456")
    db_session.add(user)
    await db_session.commit()
    return user


async def new_ticket(db_session: AsyncSession, customer: User, text: str) -> Ticket:
    """Un ticket recién creado (lo que deja TicketService.create antes de encolar)."""
    ticket = Ticket(customer_id=customer.id, text=text)
    db_session.add(ticket)
    await db_session.commit()
    return ticket


async def process(
    processor: TicketProcessor, db_session: AsyncSession, ticket: Ticket
) -> Ticket:
    await processor.process_ticket(ticket.id)
    await db_session.refresh(ticket)
    return ticket


async def messages_of(
    db_session: AsyncSession, ticket: Ticket
) -> list[tuple[str, str]]:
    rows = await db_session.scalars(
        select(TicketMessage)
        .where(TicketMessage.ticket_id == ticket.id)
        .order_by(TicketMessage.created_at, TicketMessage.id)
    )
    return [(m.author, m.content) for m in rows]


async def transitions_of(
    db_session: AsyncSession, ticket: Ticket
) -> list[tuple[str | None, str | None, str | None]]:
    rows = await db_session.scalars(
        select(TicketEvent)
        .where(
            TicketEvent.ticket_id == ticket.id,
            TicketEvent.event == TicketEventType.STATUS_CHANGED,
        )
        .order_by(TicketEvent.created_at, TicketEvent.id)
    )
    return [(e.from_status, e.to_status, e.reason) for e in rows]


# --- Caminos de la IA -----------------------------------------------------------


async def test_confident_faq_gets_its_canned_answer(
    processor: TicketProcessor,
    classifier: FakeClassifier,
    db_session: AsyncSession,
    customer: User,
) -> None:
    text = "What is the age limit to open an account?"
    classifier.answer(text, "age_limit", confidence=0.95)
    ticket = await process(
        processor, db_session, await new_ticket(db_session, customer, text)
    )

    assert ticket.status == TicketStatus.ANSWERED
    assert (ticket.intent, ticket.confidence) == ("age_limit", 0.95)
    assert ticket.ticket_type == "account_and_personal_details"
    assert (ticket.priority, ticket.priority_reason) == (1, "low by intent")
    assert (ticket.route, ticket.route_reason) == (Route.AI, router.CANNED_FAQ)
    assert ticket.classifier_version == "fake-classifier"
    assert ticket.due_at is None  # el plazo es para la cola humana
    assert await messages_of(db_session, ticket) == [
        (MessageAuthor.SYSTEM, AGE_LIMIT_ANSWER)
    ]
    assert await transitions_of(db_session, ticket) == [
        ("received", "processing", None),
        ("processing", "answered", None),
    ]


async def test_rag_intent_uses_its_canned_answer_while_rag_is_off(
    processor: TicketProcessor,
    classifier: FakeClassifier,
    db_session: AsyncSession,
    customer: User,
) -> None:
    text = "My card payment was declined"
    classifier.answer(text, "declined_card_payment")
    ticket = await process(
        processor, db_session, await new_ticket(db_session, customer, text)
    )

    assert ticket.status == TicketStatus.ANSWERED
    assert ticket.route_reason == CANNED_RAG_DISABLED
    # Sin policies/canned/declined_card_payment.md todavía: la provisional.
    assert await messages_of(db_session, ticket) == [
        (MessageAuthor.SYSTEM, PROVISIONAL_ANSWER)
    ]


# --- Caminos de persona ---------------------------------------------------------


async def test_always_human_intent_explains_why(
    processor: TicketProcessor,
    classifier: FakeClassifier,
    db_session: AsyncSession,
    customer: User,
) -> None:
    text = "I sent money to my brother 3 days ago and he hasn't got it"
    classifier.answer(text, "transfer_not_received_by_recipient")
    ticket = await process(
        processor, db_session, await new_ticket(db_session, customer, text)
    )

    reason = "intent always human: possible lost money"
    assert ticket.status == TicketStatus.IN_HUMAN_QUEUE
    assert (ticket.route, ticket.route_reason) == (Route.HUMAN, reason)
    assert ticket.priority == 2
    assert ticket.due_at == ticket.created_at + timedelta(hours=4)
    assert await messages_of(db_session, ticket) == [
        (MessageAuthor.SYSTEM, CALL_TEMPLATE)
    ]
    assert await transitions_of(db_session, ticket) == [
        ("received", "processing", None),
        ("processing", "in_human_queue", reason),
    ]


async def test_sensitive_keyword_overrides_a_confident_faq(
    processor: TicketProcessor,
    classifier: FakeClassifier,
    db_session: AsyncSession,
    customer: User,
) -> None:
    text = "Someone used my card. Also, what's the age limit?"
    classifier.answer(text, "age_limit")
    ticket = await process(
        processor, db_session, await new_ticket(db_session, customer, text)
    )

    assert ticket.status == TicketStatus.IN_HUMAN_QUEUE
    assert ticket.route_reason == router.SENSITIVE_KEYWORD
    # La prioridad sigue siendo la de la intención predicha (decidido en 3.5).
    assert ticket.due_at == ticket.created_at + timedelta(hours=24)


async def test_doubtful_ticket_goes_to_a_person(
    processor: TicketProcessor, db_session: AsyncSession, customer: User
) -> None:
    # El clasificador falso duda por defecto (0.5 / 0.5).
    ticket = await process(
        processor, db_session, await new_ticket(db_session, customer, "Hmm, hello?")
    )

    assert ticket.status == TicketStatus.IN_HUMAN_QUEUE
    assert ticket.route_reason == router.LOW_CONFIDENCE


# --- Urgencia ---------------------------------------------------------------------


async def test_urgency_raises_the_priority_and_shortens_the_deadline(
    processor: TicketProcessor,
    classifier: FakeClassifier,
    db_session: AsyncSession,
    customer: User,
) -> None:
    text = "My transfer to my brother hasn't arrived, it's urgent!"
    classifier.answer(text, "transfer_not_received_by_recipient")
    ticket = await process(
        processor, db_session, await new_ticket(db_session, customer, text)
    )

    assert ticket.priority == 3
    assert ticket.priority_reason == (
        "medium by intent, raised to high by urgency (urgent wording)"
    )
    assert ticket.due_at == ticket.created_at + timedelta(hours=1)


async def test_third_ticket_of_the_customer_is_raised(
    processor: TicketProcessor, db_session: AsyncSession, customer: User
) -> None:
    for text in ("first", "second"):
        await process(
            processor, db_session, await new_ticket(db_session, customer, text)
        )

    third = await process(
        processor, db_session, await new_ticket(db_session, customer, "third")
    )

    # card_arrival (la del clasificador falso) es low → medium.
    assert third.priority == 2
    assert "3 tickets in 24 h" in (third.priority_reason or "")


# --- Fallos y repeticiones --------------------------------------------------------


async def test_processing_error_sends_the_ticket_to_a_person(
    processor: TicketProcessor,
    classifier: FakeClassifier,
    db_session: AsyncSession,
    customer: User,
) -> None:
    classifier.error = RuntimeError("model exploded")
    ticket = await process(
        processor, db_session, await new_ticket(db_session, customer, "Help me")
    )

    assert ticket.status == TicketStatus.IN_HUMAN_QUEUE
    assert (ticket.route, ticket.route_reason) == (Route.HUMAN, PROCESSING_ERROR)
    # No se sabe qué es: podría ser un fraude → high, plazo de 1 h.
    assert ticket.priority == 3
    assert ticket.due_at == ticket.created_at + timedelta(hours=1)
    assert ticket.intent is None
    assert await messages_of(db_session, ticket) == [
        (MessageAuthor.SYSTEM, CALL_TEMPLATE)
    ]
    assert await transitions_of(db_session, ticket) == [
        ("received", "processing", None),
        ("processing", "in_human_queue", PROCESSING_ERROR),
    ]


async def test_error_halfway_does_not_leave_partial_changes(
    db_session: AsyncSession,
    classifier: FakeClassifier,
    rules: Rules,
    customer: User,
) -> None:
    # Con el RAG activado (v2) y sin RAG implementado, falla después de haber
    # rellenado intent, prioridad...: el rollback los descarta.
    text = "My card payment was declined"
    classifier.answer(text, "declined_card_payment")
    processor = make_processor(
        db_session, classifier, rules.model_copy(update={"rag_enabled": True})
    )
    ticket = await process(
        processor, db_session, await new_ticket(db_session, customer, text)
    )

    assert ticket.route_reason == PROCESSING_ERROR
    assert ticket.intent is None
    assert await messages_of(db_session, ticket) == [
        (MessageAuthor.SYSTEM, CALL_TEMPLATE)
    ]


async def test_processing_twice_does_nothing_the_second_time(
    processor: TicketProcessor, db_session: AsyncSession, customer: User
) -> None:
    # Con ARQ (v2), un trabajo puede llegar dos veces ("al menos una vez").
    ticket = await process(
        processor, db_session, await new_ticket(db_session, customer, "Hello")
    )
    await process(processor, db_session, ticket)

    assert len(await messages_of(db_session, ticket)) == 1
    assert len(await transitions_of(db_session, ticket)) == 2
