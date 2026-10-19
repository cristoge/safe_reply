"""El router: a dónde va un ticket y por qué (apartado 6 del diseño).

La única implementación: la usa el procesado del backend y también ml/ para medir
(compare_models.py, robustness.py), así las cifras describen lo que se ejecuta.

La ruta la deciden las reglas y la confianza del clasificador, nunca el LLM.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from revision_rag.models.enums import Route
from revision_rag.triage.rules import RouterConfig, Rules

# route_reason de cada regla: texto en inglés, lo ve el agente y queda en la BD.
SENSITIVE_KEYWORD = "sensitive keyword"
ALWAYS_HUMAN = "intent always human"
ALWAYS_HUMAN_LOW_CONFIDENCE = "intent always human · low confidence"
LOW_CONFIDENCE = "low confidence"
AMBIGUOUS = "ambiguous with sensitive intent"
CANNED_FAQ = "canned answer for FAQ intent"


class Destination(StrEnum):
    HUMAN = "human"
    CANNED = "canned"
    RAG = "rag"


@dataclass(frozen=True)
class Prediction:
    """Lo que el router necesita de la salida del clasificador."""

    intent: str
    confidence: float
    # Suma de las probabilidades de todas las intenciones de persona: "¿cuánto
    # se parece esto a algo delicado?", aunque gane una intención de IA.
    p_human: float


@dataclass(frozen=True)
class RouteDecision:
    destination: Destination
    # None solo en el RAG: no hay una regla que lo explique, es el camino normal.
    reason: str | None


def summarize(probabilities: Mapping[str, float], rules: Rules) -> Prediction:
    """De las probabilidades de cada intención a (intención, confianza, P(persona)).

    Si hay empate, gana la primera intención en el orden recibido (como argmax).
    """
    intent = max(probabilities, key=probabilities.__getitem__)
    p_human = sum(p for i, p in probabilities.items() if i in rules.human_intents)
    return Prediction(intent, probabilities[intent], p_human)


def min_confidence_for(
    intent: str, rules: Rules, config: RouterConfig | None = None
) -> float:
    """El umbral de una intención: el propio solo puede ser más exigente."""
    config = config or rules.router
    return max(config.min_confidence, rules.intents[intent].min_confidence or 0)


def decide_route(
    prediction: Prediction,
    text: str | None,
    rules: Rules,
    config: RouterConfig | None = None,
) -> RouteDecision:
    """Las reglas en orden; la primera que se cumple decide y da el motivo.

    0. El texto tiene una palabra sensible           → persona
    1. La intención es de "siempre persona"          → persona
    2. Confianza < umbral (o el de la intención)     → persona
    3. P(persona) ≥ max_p_human                      → persona
    4. answer_mode canned y confianza ≥ la exigida   → predefinida
    5. Si no                                         → RAG

    config None = los umbrales del YAML. En config, max_p_human o
    canned_min_confidence None desactivan su regla, y text None la 0: solo lo
    usan las comparativas de ml/; el backend pasa siempre el texto.
    """
    config = config or rules.router
    rule = rules.intents[prediction.intent]
    intent_min = min_confidence_for(prediction.intent, rules, config)
    low_confidence = prediction.confidence < intent_min

    if text is not None and rules.sensitive.search(text):
        return RouteDecision(Destination.HUMAN, SENSITIVE_KEYWORD)
    if rule.route == Route.HUMAN:
        # El destino es el mismo; el motivo avisa de que el modelo dudaba.
        reason = ALWAYS_HUMAN_LOW_CONFIDENCE if low_confidence else ALWAYS_HUMAN
        return RouteDecision(Destination.HUMAN, reason)
    if low_confidence:
        return RouteDecision(Destination.HUMAN, LOW_CONFIDENCE)
    if config.max_p_human is not None and prediction.p_human >= config.max_p_human:
        return RouteDecision(Destination.HUMAN, AMBIGUOUS)
    if (
        config.canned_min_confidence is not None
        and rule.answer_mode == "canned"
        and prediction.confidence >= max(intent_min, config.canned_min_confidence)
    ):
        return RouteDecision(Destination.CANNED, CANNED_FAQ)
    return RouteDecision(Destination.RAG, None)
