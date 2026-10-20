"""rules/intents.yaml cargado y validado: la única fuente de las reglas de negocio.

Se valida con Pydantic al cargarlo: un YAML mal escrito (una intención de persona
sin motivo, una de IA sin answer_mode, un umbral fuera de rango) impide arrancar,
en vez de que un ticket de fraude lo acabe respondiendo la IA.
"""

import re
from functools import cached_property
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from safe_reply.models.enums import Route

PriorityName = Literal["low", "medium", "high"]
# Como se guarda en tickets.priority (smallint): número para poder ordenar y
# sumar (escalar = +1).
PRIORITY_LEVELS: dict[PriorityName, int] = {"low": 1, "medium": 2, "high": 3}
PRIORITY_NAMES: dict[int, PriorityName] = {v: k for k, v in PRIORITY_LEVELS.items()}


class _Frozen(BaseModel):
    # extra="forbid": una clave mal escrita en el YAML ("min_confidense") es
    # un error, no algo que se ignora en silencio.
    model_config = ConfigDict(frozen=True, extra="forbid")


class IntentRule(_Frozen):
    ticket_type: str
    priority: PriorityName
    route: Route
    answer_mode: Literal["rag", "canned"] | None = None
    min_confidence: float | None = Field(default=None, gt=0, le=1)
    reason: str | None = None

    @model_validator(mode="after")
    def _route_fields_match(self) -> Self:
        if self.route == Route.AI and self.answer_mode is None:
            raise ValueError("una intención de IA necesita answer_mode (rag o canned)")
        if self.route == Route.HUMAN:
            # El motivo acaba en route_reason; una intención de persona nunca la
            # responde la IA, así que no puede tener answer_mode.
            if not self.reason:
                raise ValueError("una intención de persona necesita reason")
            if self.answer_mode is not None:
                raise ValueError("una intención de persona no lleva answer_mode")
        return self


class RouterConfig(_Frozen):
    """Umbrales del router. None desactiva la regla (lo usan las comparativas de ml/)."""

    min_confidence: float = Field(gt=0, le=1)
    max_p_human: float | None = Field(default=None, gt=0, le=1)
    canned_min_confidence: float | None = Field(default=None, gt=0, le=1)


class DueHours(_Frozen):
    high: int = Field(gt=0)
    medium: int = Field(gt=0)
    low: int = Field(gt=0)

    @model_validator(mode="after")
    def _grow_as_priority_drops(self) -> Self:
        if not self.high < self.medium < self.low:
            raise ValueError("los plazos tienen que crecer al bajar la prioridad")
        return self


def _compile_all(patterns: list[str], field: str) -> re.Pattern[str]:
    """Una sola expresión con todas, sin distinguir mayúsculas. Falla si alguna no compila."""
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            msg = f"{field}: {pattern!r} no es válida: {exc}"
            raise ValueError(msg) from exc
    return re.compile("|".join(patterns), re.IGNORECASE)


class UrgencyConfig(_Frozen):
    patterns: list[str] = Field(min_length=1)
    repeat_tickets: int = Field(ge=2)
    repeat_window_hours: int = Field(gt=0)

    @model_validator(mode="after")
    def _patterns_compile(self) -> Self:
        _compile_all(self.patterns, "urgency.patterns")
        return self

    @cached_property
    def pattern(self) -> re.Pattern[str]:
        return _compile_all(self.patterns, "urgency.patterns")


class HumanQueueConfig(_Frozen):
    claim_timeout_hours: int = Field(gt=0)
    max_failed_calls: int = Field(gt=0)


class AlertsConfig(_Frozen):
    window_hours: int = Field(gt=0)
    max_human_share: float = Field(gt=0, le=1)
    escalation_window_days: int = Field(gt=0)
    max_escalation_rate: float = Field(gt=0, le=1)
    min_tickets: int = Field(gt=0)
    stuck_minutes: int = Field(gt=0)


class Rules(_Frozen):
    router: RouterConfig
    sensitive_patterns: list[str] = Field(min_length=1)
    low_confidence_min_priority: PriorityName
    due_hours: DueHours
    urgency: UrgencyConfig
    human_queue: HumanQueueConfig
    alerts: AlertsConfig
    rag_enabled: bool = False
    intents: dict[str, IntentRule] = Field(min_length=1)

    @model_validator(mode="after")
    def _canned_not_below_general(self) -> Self:
        # Las predefinidas no pasan por el juez: nunca pueden exigir menos. Se
        # comprueba aquí (en el YAML) y no en RouterConfig, para que las
        # comparativas de ml/ puedan probar cualquier combinación de umbrales.
        router = self.router
        if (
            router.canned_min_confidence is not None
            and router.canned_min_confidence < router.min_confidence
        ):
            raise ValueError(
                "canned_min_confidence no puede ser menor que min_confidence"
            )
        return self

    @model_validator(mode="after")
    def _patterns_compile(self) -> Self:
        _compile_all(self.sensitive_patterns, "sensitive_patterns")
        return self

    @cached_property
    def sensitive(self) -> re.Pattern[str]:
        """La red de seguridad (regla 0 del router): todas las expresiones en una."""
        return _compile_all(self.sensitive_patterns, "sensitive_patterns")

    @cached_property
    def human_intents(self) -> frozenset[str]:
        return frozenset(i for i, r in self.intents.items() if r.route == Route.HUMAN)


def load_rules(path: Path) -> Rules:
    """Lee y valida el YAML. Lanza pydantic.ValidationError si algo no cuadra."""
    return Rules.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
