"""El contenido de policies/canned/: una respuesta revisada por intención de IA.

Contra los archivos reales, como test_intent_rules.py con el YAML: lo que se
comprueba es lo que recibe el cliente. Las cifras y las rutas de la app salen de
bank_facts.md: si una respuesta dice algo que no está ahí, o dos respuestas se
contradicen, falla aquí y no delante de un cliente (lo que dice el sistema
compromete al banco).
"""

import re
from pathlib import Path

import pytest

from safe_reply.models.enums import Route
from safe_reply.triage.answers import unreachable_message
from safe_reply.triage.rules import Rules

POLICIES = Path(__file__).parents[2] / "policies"
CANNED_DIR = POLICIES / "canned"
BANK_FACTS = (POLICIES / "bank_facts.md").read_text(encoding="utf-8")
ANSWERS = {
    path.stem: path.read_text(encoding="utf-8")
    for path in sorted(CANNED_DIR.glob("*.md"))
}

# Igual en todas: el aviso de transparencia (AI Act, art. 50) y la salida a una
# persona. Al final, para que ninguna respuesta lo pierda al editarla.
FOOTER = (
    "This is an automatic answer. If it doesn't solve your problem, reply to this "
    'message or press "I still need help" and a member of our team will call you.'
)
PLACEHOLDERS = re.compile(r"TODO|TBD|XXX|\{\{|\}\}|\[\.\.\.\]|<[a-z_]+>")
# 3, 0.5, 1,000, €5, 2%: basta con el número; el signo lo lleva el texto.
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
# Las filas de la tabla "App paths": | Qué | Ruta |
APP_PATH_ROW = re.compile(r"^\| [^|]+ \| ([^|]+ › [^|]+) \|$", re.MULTILINE)
APP_PATHS = sorted(APP_PATH_ROW.findall(BANK_FACTS), key=len, reverse=True)


def ai_intents(rules: Rules) -> set[str]:
    return {name for name, i in rules.intents.items() if i.route == Route.AI}


def unknown_app_paths(text: str) -> str:
    """Lo que queda con '›' tras quitar las rutas de bank_facts.md."""
    # De la más larga a la más corta: "Cards › New virtual card" antes que un
    # posible prefijo suyo.
    for path in APP_PATHS:
        text = text.replace(path, "")
    return "\n".join(line for line in text.splitlines() if "›" in line)


def test_every_ai_intent_has_an_answer(rules: Rules) -> None:
    assert sorted(ai_intents(rules) - ANSWERS.keys()) == []


def test_only_ai_intents_have_an_answer(rules: Rules) -> None:
    # Una respuesta para una intención de persona nunca se enviaría (o, peor, se
    # enviaría si alguien cambia la ruta en el YAML sin pensarlo); una que no
    # existe es un nombre mal escrito.
    assert sorted(ANSWERS.keys() - ai_intents(rules)) == []


def test_bank_facts_has_app_paths() -> None:
    # Si la tabla cambia de formato, el test de rutas no comprobaría nada.
    assert "Help › Contact us" in APP_PATHS
    assert "Cards › Freeze card" in APP_PATHS


@pytest.mark.parametrize("intent", sorted(ANSWERS))
def test_answer_is_finished(intent: str) -> None:
    answer = ANSWERS[intent]

    assert PLACEHOLDERS.search(answer) is None
    assert answer.strip().endswith(FOOTER)
    assert answer.strip().removesuffix(FOOTER).strip(), "solo tiene el pie"
    # El pie una sola vez: copiar y pegar a medias lo duplicaría.
    assert answer.count("I still need help") == 1


@pytest.mark.parametrize("intent", sorted(ANSWERS))
def test_answer_numbers_come_from_bank_facts(intent: str) -> None:
    facts = set(NUMBER.findall(BANK_FACTS))

    assert sorted(set(NUMBER.findall(ANSWERS[intent])) - facts) == []


@pytest.mark.parametrize("intent", sorted(ANSWERS))
def test_answer_app_paths_come_from_bank_facts(intent: str) -> None:
    assert unknown_app_paths(ANSWERS[intent]) == ""


def test_unreachable_message_uses_the_app_paths_of_bank_facts() -> None:
    # El mensaje del S5 se escribió antes que bank_facts.md: que no se separen.
    assert unknown_app_paths(unreachable_message(3, "security_and_fraud")) == ""
