"""Del texto y las probabilidades del clasificador a la decisión completa.

Reglas → urgencia → router → respuesta, sin BD ni modelo: lo usan el procesado
(services/ticket_processor.py) y la evaluación (ml/evaluate.py). Así lo que se
mide es exactamente lo que recibe el cliente.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from safe_reply.models.enums import Route
from safe_reply.triage import router
from safe_reply.triage.answers import CALL_TEMPLATE, CannedAnswers
from safe_reply.triage.router import (
    Destination,
    decide_route,
    min_confidence_for,
    summarize,
)
from safe_reply.triage.rules import Rules
from safe_reply.triage.urgency import Priority, final_priority

# route_reason del camino que no sale del router.
CANNED_RAG_DISABLED = "canned answer (RAG not enabled yet)"


@dataclass(frozen=True)
class TriageResult:
    intent: str
    confidence: float
    ticket_type: str
    priority: Priority
    destination: Destination
    route: Route
    route_reason: str
    answer: str  # lo que recibe el cliente: la plantilla de llamada o una predefinida


def triage_ticket(
    text: str,
    probabilities: Mapping[str, float],
    recent_tickets: int,
    rules: Rules,
    answers: CannedAnswers,
) -> TriageResult:
    """recent_tickets: los del cliente en la ventana del YAML, contando este."""
    prediction = summarize(probabilities, rules)
    decision = decide_route(prediction, text, rules)
    rule = rules.intents[prediction.intent]
    # Por la confianza, no por el motivo: una palabra sensible gana al "low
    # confidence" en el router, pero la intención sigue siendo una adivinanza.
    low_confidence = prediction.confidence < min_confidence_for(
        prediction.intent, rules
    )
    priority = final_priority(
        rule.priority, text, recent_tickets, rules, low_confidence=low_confidence
    )

    match decision.destination:
        case Destination.HUMAN:
            route, answer = Route.HUMAN, CALL_TEMPLATE
            reason = decision.reason
            assert reason is not None  # todas las reglas de persona dan motivo
            if reason in (router.ALWAYS_HUMAN, router.ALWAYS_HUMAN_LOW_CONFIDENCE):
                # El agente ve también por qué esa intención va siempre a
                # persona ("possible fraud").
                reason = f"{reason}: {rule.reason}"
        case Destination.CANNED:
            route, reason = Route.AI, router.CANNED_FAQ
            answer = answers.for_intent(prediction.intent)
        case Destination.RAG:
            if rules.rag_enabled:
                raise NotImplementedError("el RAG llega en la v2")
            # Mientras no haya RAG, las intenciones de problemas responden con
            # su predefinida (genérica a propósito, S6).
            route, reason = Route.AI, CANNED_RAG_DISABLED
            answer = answers.for_intent(prediction.intent)

    return TriageResult(
        intent=prediction.intent,
        confidence=prediction.confidence,
        ticket_type=rule.ticket_type,
        priority=priority,
        destination=decision.destination,
        route=route,
        route_reason=reason,
        answer=answer,
    )
