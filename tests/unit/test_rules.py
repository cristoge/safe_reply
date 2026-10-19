"""load_rules: un YAML mal formado no carga (y la app no arrancará con él)."""

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from revision_rag.triage.rules import load_rules

VALID: dict[str, Any] = {
    "router": {"min_confidence": 0.8, "max_p_human": 0.1, "canned_min_confidence": 0.9},
    "sensitive_patterns": [r"\bstolen\b"],
    "due_hours": {"high": 1, "medium": 4, "low": 24},
    "urgency": {
        "patterns": [r"\burgent\b"],
        "repeat_tickets": 3,
        "repeat_window_hours": 24,
    },
    "human_queue": {"claim_timeout_hours": 2, "max_failed_calls": 3},
    "intents": {
        "age_limit": {
            "ticket_type": "account_and_personal_details",
            "priority": "low",
            "route": "ai",
            "answer_mode": "canned",
        },
        "lost_or_stolen_card": {
            "ticket_type": "security_and_fraud",
            "priority": "high",
            "route": "human",
            "reason": "possible fraud",
        },
    },
}


def write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "intents.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_valid_rules_load(tmp_path: Path) -> None:
    rules = load_rules(write(tmp_path, VALID))

    assert rules.human_intents == {"lost_or_stolen_card"}
    assert rules.sensitive.search("My card was STOLEN")


def broken(path: str, value: Any) -> dict[str, Any]:
    """VALID con un cambio en la ruta "a.b.c" (value=... borra la clave)."""
    data = copy.deepcopy(VALID)
    *parents, last = path.split(".")
    target = data
    for key in parents:
        target = target[key]
    if value is ...:
        del target[last]
    else:
        target[last] = value
    return data


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (broken("intents.age_limit.answer_mode", ...), "answer_mode"),
        (broken("intents.lost_or_stolen_card.reason", ...), "reason"),
        (broken("intents.lost_or_stolen_card.answer_mode", "rag"), "answer_mode"),
        (broken("intents.age_limit.priority", "urgent"), "priority"),
        (broken("intents.age_limit.route", "bot"), "route"),
        (broken("intents.age_limit.min_confidense", 0.9), "min_confidense"),
        (broken("intents.age_limit.min_confidence", 1.5), "min_confidence"),
        (broken("router.min_confidence", 0), "min_confidence"),
        (broken("router.canned_min_confidence", 0.7), "canned_min_confidence"),
        (broken("due_hours.medium", 48), "plazos"),
        (broken("sensitive_patterns", ["(unclosed"]), "sensitive_patterns"),
        (broken("intents", {}), "intents"),
        (broken("urgency.patterns", ["[a-"]), "urgency.patterns"),
        (broken("urgency.repeat_tickets", 1), "repeat_tickets"),
        (broken("human_queue", ...), "human_queue"),
        (broken("human_queue.max_failed_calls", 0), "max_failed_calls"),
    ],
    ids=[
        "IA sin answer_mode",
        "persona sin reason",
        "persona con answer_mode",
        "prioridad desconocida",
        "ruta desconocida",
        "clave mal escrita",
        "umbral propio > 1",
        "umbral general 0",
        "predefinidas exigen menos",
        "plazos desordenados",
        "regex inválida",
        "sin intenciones",
        "urgencia: regex inválida",
        "urgencia: repetir desde 1",
        "sin cola humana",
        "cola: 0 llamadas",
    ],
)
def test_invalid_rules_do_not_load(
    tmp_path: Path, data: dict[str, Any], error: str
) -> None:
    with pytest.raises(ValidationError, match=error):
        load_rules(write(tmp_path, data))
