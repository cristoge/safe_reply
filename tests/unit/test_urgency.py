from datetime import UTC, datetime, timedelta

import pytest

from revision_rag.triage.rules import Rules
from revision_rag.triage.urgency import due_at, final_priority


@pytest.mark.parametrize(
    "text",
    [
        "I need this fixed, it's urgent",
        "Please help ASAP",
        "Block my card immediately",
        "This is an emergency",
        "Where is my money!!!",
        "Answer as soon as possible please",
    ],
)
def test_urgent_wording_raises_one_level(rules: Rules, text: str) -> None:
    priority = final_priority("medium", text, recent_tickets=1, rules=rules)

    assert priority.level == 3
    assert (
        priority.reason
        == "medium by intent, raised to high by urgency (urgent wording)"
    )


@pytest.mark.parametrize(
    "text",
    ["How do I change my PIN?", "My card hasn't arrived!", "I'm surgeon"],
)
def test_normal_tickets_keep_the_base_priority(rules: Rules, text: str) -> None:
    priority = final_priority("low", text, recent_tickets=1, rules=rules)

    assert (priority.level, priority.reason) == (1, "low by intent")


def test_the_third_ticket_in_the_window_raises_one_level(rules: Rules) -> None:
    second = final_priority("low", "Any news?", recent_tickets=2, rules=rules)
    third = final_priority("low", "Any news?", recent_tickets=3, rules=rules)

    assert second.level == 1
    assert third.level == 2
    assert (
        third.reason == "low by intent, raised to medium by urgency (3 tickets in 24 h)"
    )


def test_several_signals_only_raise_one_level(rules: Rules) -> None:
    # La prioridad ordena la cola, no castiga: urgente y repetido = +1, no +2.
    priority = final_priority("low", "urgent!!!", recent_tickets=5, rules=rules)

    assert priority.level == 2
    assert "urgent wording, 5 tickets in 24 h" in priority.reason


def test_high_stays_high_but_says_why(rules: Rules) -> None:
    priority = final_priority("high", "urgent", recent_tickets=1, rules=rules)

    assert (priority.level, priority.reason) == (3, "high by intent (urgent wording)")


@pytest.mark.parametrize(("level", "hours"), [(3, 1), (2, 4), (1, 24)])
def test_due_at_depends_on_the_final_priority(
    rules: Rules, level: int, hours: int
) -> None:
    created = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)

    assert due_at(created, level, rules) == created + timedelta(hours=hours)
