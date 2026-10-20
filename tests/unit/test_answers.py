from pathlib import Path

import pytest

from safe_reply.triage.answers import (
    CALL_TEMPLATE,
    CannedAnswers,
    unreachable_message,
)


def test_loads_one_answer_per_markdown_file(tmp_path: Path) -> None:
    (tmp_path / "age_limit.md").write_text("You must be 18.\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not an answer", encoding="utf-8")

    answers = CannedAnswers.from_dir(tmp_path)

    assert len(answers) == 1
    assert answers.for_intent("age_limit") == "You must be 18."


def test_an_intent_without_answer_has_no_fallback_text(tmp_path: Path) -> None:
    # Sin texto de reserva: el procesado lo convierte en "processing error" y el
    # ticket va a persona (load_triage ya impide arrancar así).
    answers = CannedAnswers.from_dir(tmp_path)

    with pytest.raises(KeyError):
        answers.for_intent("country_support")


def test_missing_directory_means_no_answers(tmp_path: Path) -> None:
    # No lanza aquí: el error, con lo que falta, lo da load_triage.
    answers = CannedAnswers.from_dir(tmp_path / "policies" / "canned")

    assert len(answers) == 0


def test_call_template_warns_about_vishing() -> None:
    # El cliente va a recibir una llamada "del banco": el fraude más común.
    assert "never ask for your PIN" in CALL_TEMPLATE
    assert "as soon as possible" in CALL_TEMPLATE  # sin plazos que comprometan


def test_unreachable_message_says_how_to_reach_the_bank() -> None:
    message = unreachable_message(3, "cards_ordering_and_activation")

    assert message.startswith("We tried to call you 3 times")
    # Que llame él al número de la app: así no puede ser una llamada falsa.
    assert "Help › Contact us" in message
    assert "branch" not in message  # banco digital: sin sucursales
    assert "freeze" not in message


def test_unreachable_message_for_fraud_says_to_freeze_the_card() -> None:
    # Mientras no hablamos con él, lo urgente es parar el daño.
    message = unreachable_message(3, "security_and_fraud")

    assert message.endswith("freeze it now in the app (Cards › Freeze card).")
