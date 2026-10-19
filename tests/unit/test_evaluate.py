"""Las cuentas de ml/evaluate.py, con resultados escritos a mano.

Sin el modelo: lo que se comprueba es que la métrica clave (respuestas
incorrectas enviadas) y las demás cuentan lo que dicen contar.
"""

from evaluate import labelled_metrics, unlabelled_metrics

from revision_rag.models.enums import Route
from revision_rag.triage.answers import CALL_TEMPLATE
from revision_rag.triage.pipeline import CANNED_RAG_DISABLED, TriageResult
from revision_rag.triage.router import CANNED_FAQ, LOW_CONFIDENCE, Destination
from revision_rag.triage.rules import Rules
from revision_rag.triage.urgency import Priority


def result(
    intent: str, rules: Rules, *, to_ai: bool, reason: str, level: int = 1
) -> TriageResult:
    return TriageResult(
        intent=intent,
        confidence=0.95,
        ticket_type=rules.intents[intent].ticket_type,
        priority=Priority(level, "test"),
        destination=Destination.CANNED if to_ai else Destination.HUMAN,
        route=Route.AI if to_ai else Route.HUMAN,
        route_reason=reason,
        answer="answer" if to_ai else CALL_TEMPLATE,
    )


def test_counts_what_reaches_the_customer(rules: Rules) -> None:
    texts = ["t1", "t2", "t3", "t4"]
    expected = ["lost_or_stolen_card", "age_limit", "country_support", "age_limit"]
    results = [
        # Un fraude respondido por la IA: la métrica clave.
        result("card_arrival", rules, to_ai=True, reason=CANNED_RAG_DISABLED),
        # Bien respondido.
        result("age_limit", rules, to_ai=True, reason=CANNED_FAQ),
        # Mal, pero del mismo tipo (account_and_personal_details).
        result("age_limit", rules, to_ai=True, reason=CANNED_FAQ),
        # Lo podía responder la IA y acaba en llamada.
        result("age_limit", rules, to_ai=False, reason=LOW_CONFIDENCE),
    ]

    metrics = labelled_metrics(texts, expected, results, rules)

    assert metrics["wrong_answers_sent"] == 1
    assert metrics["wrong_answers_sent_examples"] == [
        {"expected": "lost_or_stolen_card", "predicted": "card_arrival", "text": "t1"}
    ]
    assert (metrics["calls"], metrics["ai_answers"]) == (0.25, 0.75)
    assert (metrics["canned_faq"], metrics["canned_rag_off"]) == (0.5, 0.25)
    assert metrics["ai_wrong_intent"] == 2
    assert metrics["ai_wrong_intent_other_type"] == 1  # el fraude → tarjetas
    assert metrics["avoidable_calls"] == 0.25
    assert metrics["human_reasons"] == {LOW_CONFIDENCE: 1}


def test_queue_priority_only_counts_tickets_sent_to_a_person(rules: Rules) -> None:
    # lost_or_stolen_card es high (3): en la cola con low, espera de más.
    expected = ["lost_or_stolen_card", "age_limit"]
    results = [
        result("age_limit", rules, to_ai=False, reason=LOW_CONFIDENCE, level=1),
        result("age_limit", rules, to_ai=True, reason=CANNED_FAQ, level=1),
    ]

    metrics = labelled_metrics(["t1", "t2"], expected, results, rules)

    assert metrics["queue_priority_ok"] == 0.0
    assert metrics["queue_priority_too_low"] == 1
    assert metrics["queue_priority_too_high"] == 0


def test_unlabelled_sets_count_what_the_ai_answered(rules: Rules) -> None:
    results = [
        result("age_limit", rules, to_ai=True, reason=CANNED_FAQ),
        result("age_limit", rules, to_ai=False, reason=LOW_CONFIDENCE),
    ]

    metrics = unlabelled_metrics(["hello", "what's the weather"], results)

    assert metrics["answered_by_ai"] == 1
    assert metrics["answered_by_ai_examples"][0]["text"] == "hello"
