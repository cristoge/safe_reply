"""triage.router: cada regla, su orden y su route_reason.

Con unas reglas propias y pequeñas (no el YAML real): los tests no cambian si
cambia una regla de negocio, solo si cambia cómo decide el router.
"""

import pytest

from revision_rag.triage import router
from revision_rag.triage.router import Destination, Prediction, decide_route, summarize
from revision_rag.triage.rules import RouterConfig, Rules

RULES = Rules.model_validate(
    {
        "router": {
            "min_confidence": 0.8,
            "max_p_human": 0.1,
            "canned_min_confidence": 0.9,
        },
        "sensitive_patterns": [r"\bstolen\b"],
        "due_hours": {"high": 1, "medium": 4, "low": 24},
        "urgency": {
            "patterns": [r"\burgent\b"],
            "repeat_tickets": 3,
            "repeat_window_hours": 24,
        },
        "intents": {
            "age_limit": {  # FAQ con predefinida
                "ticket_type": "account_and_personal_details",
                "priority": "low",
                "route": "ai",
                "answer_mode": "canned",
            },
            "declined_card_payment": {  # problema: RAG
                "ticket_type": "cards_usage_problems",
                "priority": "medium",
                "route": "ai",
                "answer_mode": "rag",
            },
            "exchange_rate": {  # umbral propio más exigente
                "ticket_type": "currency_exchange",
                "priority": "low",
                "route": "ai",
                "answer_mode": "canned",
                "min_confidence": 0.95,
            },
            "lost_or_stolen_card": {
                "ticket_type": "security_and_fraud",
                "priority": "high",
                "route": "human",
                "reason": "possible fraud",
            },
        },
    }
)


def decide(
    intent: str,
    confidence: float,
    p_human: float = 0.0,
    text: str | None = "a normal ticket",
    config: RouterConfig | None = None,
) -> tuple[Destination, str | None]:
    decision = decide_route(
        Prediction(intent, confidence, p_human), text, RULES, config
    )
    return decision.destination, decision.reason


# --- Cada regla -----------------------------------------------------------------


def test_rule_0_sensitive_keyword_beats_everything() -> None:
    # Una FAQ con confianza máxima, pero el texto habla de un robo.
    assert decide("age_limit", 0.99, text="my card was stolen, age limit?") == (
        Destination.HUMAN,
        router.SENSITIVE_KEYWORD,
    )


def test_rule_1_always_human_intent() -> None:
    assert decide("lost_or_stolen_card", 0.95) == (
        Destination.HUMAN,
        router.ALWAYS_HUMAN,
    )


def test_rule_1_says_when_the_model_doubted() -> None:
    assert decide("lost_or_stolen_card", 0.5) == (
        Destination.HUMAN,
        router.ALWAYS_HUMAN_LOW_CONFIDENCE,
    )


def test_rule_2_low_confidence() -> None:
    assert decide("declined_card_payment", 0.79) == (
        Destination.HUMAN,
        router.LOW_CONFIDENCE,
    )


def test_rule_2_uses_the_stricter_intent_threshold() -> None:
    # 0.9 pasa el umbral general (0.8) pero no el de la intención (0.95).
    assert decide("exchange_rate", 0.9) == (Destination.HUMAN, router.LOW_CONFIDENCE)


def test_rule_3_ambiguous_with_a_sensitive_intent() -> None:
    # Gana una de IA con confianza, pero hay un 10 % de que sea algo delicado.
    assert decide("declined_card_payment", 0.85, p_human=0.1) == (
        Destination.HUMAN,
        router.AMBIGUOUS,
    )


def test_rule_4_canned_answer_for_confident_faq() -> None:
    assert decide("age_limit", 0.9) == (Destination.CANNED, router.CANNED_FAQ)


def test_faq_below_the_canned_threshold_goes_to_rag() -> None:
    # Pasa el umbral general pero no el de las predefinidas (0.9).
    assert decide("age_limit", 0.85) == (Destination.RAG, None)


def test_canned_threshold_never_below_the_intent_threshold() -> None:
    assert decide("exchange_rate", 0.96) == (Destination.CANNED, router.CANNED_FAQ)


def test_rule_5_rag_for_problems() -> None:
    assert decide("declined_card_payment", 0.99, p_human=0.05) == (
        Destination.RAG,
        None,
    )


# --- Lo que usan las comparativas de ml/ -----------------------------------------


def test_without_text_rule_0_is_off() -> None:
    assert decide("age_limit", 0.99, text=None) == (
        Destination.CANNED,
        router.CANNED_FAQ,
    )


def test_config_overrides_and_none_disables_rules_3_and_4() -> None:
    config = RouterConfig(min_confidence=0.5)

    assert decide("declined_card_payment", 0.6, p_human=0.5, config=config) == (
        Destination.RAG,
        None,
    )
    assert decide("age_limit", 0.99, config=config) == (Destination.RAG, None)


# --- summarize --------------------------------------------------------------------


def test_summarize_picks_the_top_intent_and_adds_up_p_human() -> None:
    prediction = summarize(
        {"declined_card_payment": 0.6, "lost_or_stolen_card": 0.3, "age_limit": 0.1},
        RULES,
    )

    assert prediction.intent == "declined_card_payment"
    assert prediction.confidence == 0.6
    assert prediction.p_human == pytest.approx(0.3)


def test_summarize_breaks_ties_like_argmax() -> None:
    prediction = summarize({"age_limit": 0.5, "declined_card_payment": 0.5}, RULES)

    assert prediction.intent == "age_limit"
