"""Las rutas del agente: cola, reclamar, llamar, resolver y corregir."""

import uuid

import pytest
from fakes import FakeClassifier
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.models import TicketMessage
from revision_rag.models.enums import MessageAuthor, MessageStatus
from revision_rag.triage.answers import CALL_TEMPLATE
from revision_rag.triage.rules import Rules

TICKETS_URL = "/api/v1/tickets"
AGENT_URL = "/api/v1/agent"
TEXT = "My card hasn't arrived yet"


async def create_ticket(
    client: AsyncClient, headers: dict[str, str], text: str = TEXT
) -> dict:
    # El clasificador falso duda por defecto → persona.
    response = await client.post(TICKETS_URL, json={"text": text}, headers=headers)
    assert response.status_code == 202
    return response.json()


async def claim(client: AsyncClient, headers: dict[str, str]) -> dict:
    response = await client.post(f"{AGENT_URL}/queue/claim", headers=headers)
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/queue"),
        ("POST", "/queue/claim"),
        ("GET", "/tickets"),
        ("GET", f"/tickets/{uuid.uuid7()}"),
        ("POST", f"/tickets/{uuid.uuid7()}/contacted"),
        ("POST", f"/tickets/{uuid.uuid7()}/resolve"),
    ],
)
async def test_customers_cannot_use_agent_endpoints(
    client: AsyncClient, auth_headers: dict[str, str], method: str, path: str
) -> None:
    response = await client.request(method, f"{AGENT_URL}{path}", headers=auth_headers)

    assert response.status_code == 403


async def test_claiming_from_an_empty_queue_returns_204(
    client: AsyncClient, agent_headers: dict[str, str]
) -> None:
    response = await client.post(f"{AGENT_URL}/queue/claim", headers=agent_headers)

    assert response.status_code == 204
    assert response.content == b""


async def test_queue_shows_the_classification_for_the_agent(
    client: AsyncClient, auth_headers: dict[str, str], agent_headers: dict[str, str]
) -> None:
    body = await create_ticket(client, auth_headers)

    response = await client.get(f"{AGENT_URL}/queue", headers=agent_headers)

    assert response.status_code == 200
    [ticket] = response.json()
    assert ticket["id"] == body["id"]
    assert ticket["route_reason"] == "low confidence"
    assert ticket["priority"] is not None and ticket["due_at"] is not None
    assert ticket["assigned_agent_id"] is None


async def test_claim_returns_everything_needed_to_call(
    client: AsyncClient, auth_headers: dict[str, str], agent_headers: dict[str, str]
) -> None:
    body = await create_ticket(client, auth_headers)

    ticket = await claim(client, agent_headers)

    assert ticket["id"] == body["id"]
    assert ticket["assigned_agent_id"] is not None
    assert ticket["customer"]["phone"] == "+34600123456"
    assert [m["content"] for m in ticket["messages"]] == [CALL_TEMPLATE]
    assert [e["event"] for e in ticket["events"]][-1] == "assigned"
    # Ya no está libre: un segundo "dame el siguiente" no lo devuelve.
    again = await client.post(f"{AGENT_URL}/queue/claim", headers=agent_headers)
    assert again.status_code == 204


async def test_the_whole_cycle_of_a_call(
    client: AsyncClient, auth_headers: dict[str, str], agent_headers: dict[str, str]
) -> None:
    ticket_id = (await create_ticket(client, auth_headers))["id"]
    await claim(client, agent_headers)

    mine = await client.get(f"{AGENT_URL}/tickets", headers=agent_headers)
    assert [t["id"] for t in mine.json()] == [ticket_id]

    contacted = await client.post(
        f"{AGENT_URL}/tickets/{ticket_id}/contacted", headers=agent_headers
    )
    resolved = await client.post(
        f"{AGENT_URL}/tickets/{ticket_id}/resolve", headers=agent_headers
    )

    assert contacted.status_code == 200
    assert contacted.json()["status"] == "contacted"
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    # Resuelto: ya no está entre los que el agente tiene entre manos.
    mine = await client.get(f"{AGENT_URL}/tickets", headers=agent_headers)
    assert mine.json() == []
    customer_view = await client.get(f"{TICKETS_URL}/{ticket_id}", headers=auth_headers)
    assert customer_view.json()["status"] == "resolved"


async def test_resolving_without_contact_needs_a_reason(
    client: AsyncClient, auth_headers: dict[str, str], agent_headers: dict[str, str]
) -> None:
    ticket_id = (await create_ticket(client, auth_headers, "hello"))["id"]
    await claim(client, agent_headers)
    url = f"{AGENT_URL}/tickets/{ticket_id}/resolve"

    without = await client.post(url, headers=agent_headers)
    blank = await client.post(url, json={"reason": "  "}, headers=agent_headers)
    with_reason = await client.post(
        url, json={"reason": "not a bank issue"}, headers=agent_headers
    )

    assert without.status_code == 422
    assert "motivo" in without.json()["detail"]
    assert blank.status_code == 422
    assert with_reason.status_code == 200
    assert with_reason.json()["status"] == "resolved"


async def test_failed_calls_tell_the_customer_and_free_the_ticket(
    client: AsyncClient,
    auth_headers: dict[str, str],
    agent_headers: dict[str, str],
    rules: Rules,
) -> None:
    ticket_id = (await create_ticket(client, auth_headers))["id"]
    await claim(client, agent_headers)
    url = f"{AGENT_URL}/tickets/{ticket_id}/call-failed"

    responses = [
        await client.post(url, headers=agent_headers)
        for _ in range(rules.human_queue.max_failed_calls)
    ]

    assert [r.status_code for r in responses] == [200] * len(responses)
    assert responses[-1].json()["assigned_agent_id"] is None
    customer_view = await client.get(f"{TICKETS_URL}/{ticket_id}", headers=auth_headers)
    assert customer_view.json()["messages"][-1]["content"].startswith(
        "We tried to call you"
    )
    # Ya no es suyo: otra llamada fallida sin reclamarlo de nuevo → 403.
    assert (await client.post(url, headers=agent_headers)).status_code == 403
    # Vuelve a estar en la cola para quien la atienda.
    assert (await claim(client, agent_headers))["id"] == ticket_id


async def test_another_agent_cannot_act_on_my_ticket(
    client: AsyncClient,
    auth_headers: dict[str, str],
    agent_headers: dict[str, str],
    other_agent_headers: dict[str, str],
) -> None:
    ticket_id = (await create_ticket(client, auth_headers))["id"]
    await claim(client, agent_headers)

    response = await client.post(
        f"{AGENT_URL}/tickets/{ticket_id}/contacted", headers=other_agent_headers
    )

    assert response.status_code == 403
    assert "reclámalo" in response.json()["detail"]


async def test_actions_on_an_answered_ticket_return_409(
    client: AsyncClient,
    auth_headers: dict[str, str],
    agent_headers: dict[str, str],
    classifier: FakeClassifier,
) -> None:
    text = "What is the age limit to open an account?"
    classifier.answer(text, "age_limit")
    ticket_id = (await create_ticket(client, auth_headers, text))["id"]

    response = await client.post(
        f"{AGENT_URL}/tickets/{ticket_id}/contacted", headers=agent_headers
    )

    assert response.status_code == 409


async def test_agent_detail_includes_drafts_and_the_customer_reply(
    client: AsyncClient,
    auth_headers: dict[str, str],
    agent_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    ticket_id = (await create_ticket(client, auth_headers))["id"]
    db_session.add(
        TicketMessage(
            ticket_id=uuid.UUID(ticket_id),
            author=MessageAuthor.AI,
            content="Draft the judge rejected",
            status=MessageStatus.DRAFT,
            judge_passed=False,
        )
    )
    await db_session.flush()
    await client.post(
        f"{TICKETS_URL}/{ticket_id}/messages",
        json={"text": "Call me after 6 pm"},
        headers=auth_headers,
    )

    response = await client.get(
        f"{AGENT_URL}/tickets/{ticket_id}", headers=agent_headers
    )

    assert response.status_code == 200
    detail = response.json()
    assert detail["text"] == TEXT
    assert [(m["author"], m["status"]) for m in detail["messages"]] == [
        ("system", "sent"),
        ("ai", "draft"),
        ("customer", "sent"),
    ]
    assert [e["event"] for e in detail["events"]][-1] == "customer_replied"


async def test_agent_detail_of_a_missing_ticket_returns_404(
    client: AsyncClient, agent_headers: dict[str, str]
) -> None:
    response = await client.get(
        f"{AGENT_URL}/tickets/{uuid.uuid7()}", headers=agent_headers
    )

    assert response.status_code == 404


async def test_corrections(
    client: AsyncClient, auth_headers: dict[str, str], agent_headers: dict[str, str]
) -> None:
    ticket_id = (await create_ticket(client, auth_headers))["id"]
    url = f"{AGENT_URL}/tickets/{ticket_id}/corrections"

    created = await client.post(
        url,
        json={"corrected_intent": "card_arrival", "note": "clear enough"},
        headers=agent_headers,
    )
    unknown = await client.post(
        url, json={"corrected_intent": "card_stuck_in_atm"}, headers=agent_headers
    )
    bad_priority = await client.post(
        url, json={"corrected_priority": 4}, headers=agent_headers
    )

    assert created.status_code == 201
    correction = created.json()
    # El clasificador falso dudaba: predijo card_arrival con 0.5.
    assert correction["predicted_intent"] == "card_arrival"
    assert correction["corrected_intent"] == "card_arrival"
    assert correction["predicted_route"] == "human"
    assert unknown.status_code == 422
    assert bad_priority.status_code == 422
    detail = await client.get(f"{AGENT_URL}/tickets/{ticket_id}", headers=agent_headers)
    assert len(detail.json()["corrections"]) == 1
