"""El contenido de rules/intents.yaml: está completo y es coherente con el diseño.

La estructura (campos obligatorios, valores válidos, umbrales en rango) la valida
load_rules con Pydantic: un YAML mal formado no carga (ver test_rules.py). Aquí se
comprueban las decisiones de negocio: que un ticket de fraude nunca lo responda la IA.
"""

from pathlib import Path

import pytest

from revision_rag.models.enums import Route
from revision_rag.triage.rules import Rules, load_rules

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
def rules() -> Rules:
    # Si el YAML no es válido, load_rules lanza y fallan todos los tests.
    return load_rules(RULES_PATH)


def test_has_exactly_the_77_banking77_intents(rules: Rules) -> None:
    assert len(rules.intents) == 77


def test_every_ticket_type_is_known_and_used(rules: Rules) -> None:
    assert {r.ticket_type for r in rules.intents.values()} == TICKET_TYPES


def test_sensitive_types_always_go_to_a_person(rules: Rules) -> None:
    for intent, r in rules.intents.items():
        if r.ticket_type in ALWAYS_HUMAN_TYPES:
            assert r.route == Route.HUMAN, intent


@pytest.mark.parametrize("intent", sorted(ALWAYS_HUMAN_EXCEPTIONS))
def test_exceptions_always_go_to_a_person(rules: Rules, intent: str) -> None:
    assert rules.intents[intent].route == Route.HUMAN


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
def test_sensitive_patterns_catch_fraud(rules: Rules, text: str) -> None:
    assert rules.sensitive.search(text)


@pytest.mark.parametrize(
    "text",
    [
        "My card never arrived",  # card_arrival: la responde la IA
        "How do I change my PIN?",
        "What is the exchange rate for dollars?",
        "Can I get a virtual card?",
    ],
)
def test_sensitive_patterns_ignore_normal_tickets(rules: Rules, text: str) -> None:
    assert not rules.sensitive.search(text)
