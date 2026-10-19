"""Prioridad final y plazo de un ticket (apartado 4 del diseño).

La prioridad base sale de la intención (YAML); la urgencia del mensaje o que el
cliente insista la suben un nivel. El plazo (due_at) depende de la prioridad
final: la cola humana se ordena por plazo, no por prioridad (sin inanición).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from revision_rag.triage.rules import (
    PRIORITY_LEVELS,
    PRIORITY_NAMES,
    PriorityName,
    Rules,
)

HIGHEST = max(PRIORITY_LEVELS.values())


@dataclass(frozen=True)
class Priority:
    level: int  # 1 = low · 2 = medium · 3 = high (tickets.priority)
    reason: str  # tickets.priority_reason, en inglés: lo lee el agente


def final_priority(
    base: PriorityName,
    text: str,
    recent_tickets: int,
    rules: Rules,
    *,
    low_confidence: bool = False,
) -> Priority:
    """La prioridad base, con el mínimo si el modelo dudaba y +1 si hay urgencia.

    recent_tickets: los tickets del cliente en la ventana del YAML, contando este.
    Varias señales no suben más de un nivel: la prioridad ordena, no castiga.
    """
    level = PRIORITY_LEVELS[base]
    reason = f"{base} by intent"
    floor = PRIORITY_LEVELS[rules.low_confidence_min_priority]
    if low_confidence and level < floor:
        # La base sale de una intención adivinada con poca confianza.
        level = floor
        reason += f", raised to {PRIORITY_NAMES[floor]} (low confidence)"

    urgency = rules.urgency
    signals = []
    if urgency.pattern.search(text):
        signals.append("urgent wording")
    if recent_tickets >= urgency.repeat_tickets:
        signals.append(f"{recent_tickets} tickets in {urgency.repeat_window_hours} h")
    if not signals:
        return Priority(level, reason)
    why = ", ".join(signals)
    if level == HIGHEST:
        return Priority(level, f"{reason} ({why})")
    raised = level + 1
    return Priority(
        raised, f"{reason}, raised to {PRIORITY_NAMES[raised]} by urgency ({why})"
    )


def due_at(start: datetime, level: int, rules: Rules) -> datetime:
    """Plazo para atender el ticket: start + las horas de su prioridad (YAML)."""
    hours: int = getattr(rules.due_hours, PRIORITY_NAMES[level])
    return start + timedelta(hours=hours)


def escalated_priority(level: int, reason: str | None) -> Priority:
    """ "I still need help": un nivel más que la que tenía (como mucho, high).

    El motivo anterior se conserva: el agente ve de dónde venía la prioridad.
    """
    if level == HIGHEST:
        return Priority(level, f"{reason} (escalated)")
    raised = level + 1
    return Priority(
        raised, f"{reason}, raised to {PRIORITY_NAMES[raised]} by escalation"
    )
