"""Los logs de un ticket de principio a fin: qué eventos salen y qué nunca sale."""

import json
import logging

import pytest
from fakes import FakeClassifier
from httpx import AsyncClient

from revision_rag.core.logging import JsonFormatter

TICKETS_URL = "/api/v1/tickets"
AGENT_URL = "/api/v1/agent"
# Datos personales que podrían colarse en un log si alguien loguea el texto.
TEXT = "My card hasn't arrived, my IBAN is ES9121000418450200051332"
REPLY = "Call me at +34611222333 after 6 pm"
REASON = "Customer Jane Doe, duplicated ticket"


def lines(caplog: pytest.LogCaptureFixture) -> list[dict]:
    formatter = JsonFormatter()
    return [
        json.loads(formatter.format(r))
        for r in caplog.records
        if r.name.startswith("revision_rag")
    ]


async def test_a_ticket_leaves_one_event_per_step_and_no_personal_data(
    client: AsyncClient,
    auth_headers: dict[str, str],
    agent_headers: dict[str, str],
    classifier: FakeClassifier,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="revision_rag")
    ticket_id = (
        await client.post(TICKETS_URL, json={"text": TEXT}, headers=auth_headers)
    ).json()["id"]
    await client.post(
        f"{TICKETS_URL}/{ticket_id}/messages",
        json={"text": REPLY},
        headers=auth_headers,
    )
    await client.post(f"{AGENT_URL}/queue/claim", headers=agent_headers)
    await client.post(
        f"{AGENT_URL}/tickets/{ticket_id}/call-failed", headers=agent_headers
    )
    await client.post(
        f"{AGENT_URL}/tickets/{ticket_id}/resolve",
        json={"reason": REASON},
        headers=agent_headers,
    )

    entries = lines(caplog)

    assert [e["event"] for e in entries] == [
        "ticket_created",
        "ticket_triaged",
        "customer_replied",
        "ticket_claimed",
        "call_failed",
        "ticket_resolved",
    ]
    assert all(e["ticket_id"] == ticket_id for e in entries)
    triaged = entries[1]
    assert triaged["route"] == "human"
    assert triaged["route_reason"] == "low confidence"
    assert triaged["classifier_version"] == classifier.version
    assert triaged["duration_ms"] >= 0
    assert entries[3]["reclaimed"] is False
    assert entries[5]["contacted"] is False
    everything = "\n".join(json.dumps(e) for e in entries)
    for personal in (TEXT, "ES9121000418450200051332", "+34611222333", "Jane Doe"):
        assert personal not in everything


async def test_escalating_logs_which_answer_did_not_help(
    client: AsyncClient,
    auth_headers: dict[str, str],
    classifier: FakeClassifier,
    caplog: pytest.LogCaptureFixture,
) -> None:
    text = "What is the age limit to open an account?"
    classifier.answer(text, "age_limit")
    ticket_id = (
        await client.post(TICKETS_URL, json={"text": text}, headers=auth_headers)
    ).json()["id"]
    caplog.set_level(logging.INFO, logger="revision_rag")

    await client.post(f"{TICKETS_URL}/{ticket_id}/escalate", headers=auth_headers)

    [escalated] = lines(caplog)
    assert escalated["event"] == "ticket_escalated"
    assert (escalated["by"], escalated["intent"]) == ("button", "age_limit")
    assert escalated["route_reason"] == "canned answer for FAQ intent"


async def test_processing_errors_are_logged_with_the_traceback(
    client: AsyncClient,
    auth_headers: dict[str, str],
    classifier: FakeClassifier,
    caplog: pytest.LogCaptureFixture,
) -> None:
    classifier.error = RuntimeError("model exploded")
    caplog.set_level(logging.INFO, logger="revision_rag")

    await client.post(TICKETS_URL, json={"text": TEXT}, headers=auth_headers)

    failed = next(e for e in lines(caplog) if e["event"] == "ticket_processing_failed")
    assert failed["level"] == "ERROR"
    assert "model exploded" in failed["exc_info"]
