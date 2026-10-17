"""El fichero de reglas de negocio (rules/intents.yaml) está completo y es coherente.

Un error aquí no rompe nada al arrancar: haría que un ticket de fraude lo respondiera
la IA. Por eso se comprueba en los tests y no solo a mano.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

RULES_PATH = Path(__file__).parents[2] / "rules" / "intents.yaml"

TICKET_TYPES = {
    "security_and_fraud",
    "wrong_charges_and_refunds",
    "cards_ordering_and_activation",
    "cards_usage_problems",
    "atms_and_cash",
    "transfers",
    "top_ups",
    "currency_exchange",
    "identity_verification",
    "account_and_personal_details",
}
PRIORITIES = {"low", "medium", "high"}

# Excepciones del apartado 5 del diseño: siempre persona aunque su tipo lo responda la IA
ALWAYS_HUMAN_EXCEPTIONS = {
    "transfer_not_received_by_recipient",
    "failed_transfer",
    "unable_to_verify_identity",
    "verify_source_of_funds",
    "passcode_forgotten",
    "terminate_account",
}
ALWAYS_HUMAN_TYPES = {"security_and_fraud", "wrong_charges_and_refunds"}


@pytest.fixture(scope="module")
def rules() -> dict[str, Any]:
    return yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))


def test_has_exactly_the_77_banking77_intents(rules: dict[str, Any]) -> None:
    assert len(rules["intents"]) == 77


def test_every_ticket_type_is_known_and_used(rules: dict[str, Any]) -> None:
    used = {r["ticket_type"] for r in rules["intents"].values()}
    assert used == TICKET_TYPES


@pytest.mark.parametrize("field", ["ticket_type", "priority", "route"])
def test_required_fields(rules: dict[str, Any], field: str) -> None:
    missing = [intent for intent, r in rules["intents"].items() if field not in r]
    assert not missing


def test_values_are_valid(rules: dict[str, Any]) -> None:
    for intent, r in rules["intents"].items():
        assert r["priority"] in PRIORITIES, intent
        assert r["route"] in {"ai", "human"}, intent
        assert 0 < r.get("min_confidence", 1) <= 1, intent


def test_ai_intents_say_how_they_are_answered(rules: dict[str, Any]) -> None:
    for intent, r in rules["intents"].items():
        if r["route"] == "ai":
            assert r.get("answer_mode") in {"rag", "canned"}, intent


def test_human_intents_have_a_reason_and_no_answer_mode(rules: dict[str, Any]) -> None:
    # El motivo acaba en route_reason; una intención de persona nunca la responde la IA
    for intent, r in rules["intents"].items():
        if r["route"] == "human":
            assert r.get("reason"), intent
            assert "answer_mode" not in r, intent


def test_sensitive_types_always_go_to_a_person(rules: dict[str, Any]) -> None:
    for intent, r in rules["intents"].items():
        if r["ticket_type"] in ALWAYS_HUMAN_TYPES:
            assert r["route"] == "human", intent


@pytest.mark.parametrize("intent", sorted(ALWAYS_HUMAN_EXCEPTIONS))
def test_exceptions_always_go_to_a_person(rules: dict[str, Any], intent: str) -> None:
    assert rules["intents"][intent]["route"] == "human"


def test_router_thresholds(rules: dict[str, Any]) -> None:
    router = rules["router"]
    assert 0 < router["min_confidence"] <= 1
    assert 0 < router["max_p_human"] <= 1
    # Las predefinidas no pasan por el juez: nunca pueden exigir menos que el resto
    assert router["canned_min_confidence"] >= router["min_confidence"]


def test_due_hours_grow_as_priority_drops(rules: dict[str, Any]) -> None:
    due = rules["due_hours"]
    assert 0 < due["high"] < due["medium"] < due["low"]


@pytest.fixture(scope="module")
def sensitive(rules: dict[str, Any]) -> re.Pattern[str]:
    return re.compile("|".join(rules["sensitive_patterns"]), re.IGNORECASE)


@pytest.mark.parametrize(
    "text",
    [
        "My card might be compromised, can I get a disposable virtual card?",
        "My card was stolen yesterday and I also want to know how to change my PIN",
        "There's a payment I didn't make",
        "It wasn't me who withdrew that cash",
        "I don't recognise this direct debit",
        "Someone used my card in another country",
        "My transfer to my brother never arrived",
        "This looks like FRAUD",
    ],
)
def test_sensitive_patterns_catch_fraud(sensitive: re.Pattern[str], text: str) -> None:
    assert sensitive.search(text)


@pytest.mark.parametrize(
    "text",
    [
        "My card never arrived",  # card_arrival: la responde la IA
        "How do I change my PIN?",
        "What is the exchange rate for dollars?",
        "Can I get a virtual card?",
    ],
)
def test_sensitive_patterns_ignore_normal_tickets(
    sensitive: re.Pattern[str], text: str
) -> None:
    assert not sensitive.search(text)
