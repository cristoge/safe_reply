from pathlib import Path

from revision_rag.triage.answers import CALL_TEMPLATE, PROVISIONAL_ANSWER, CannedAnswers


def test_loads_one_answer_per_markdown_file(tmp_path: Path) -> None:
    (tmp_path / "age_limit.md").write_text("You must be 18.\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not an answer", encoding="utf-8")

    answers = CannedAnswers.from_dir(tmp_path)

    assert len(answers) == 1
    assert answers.for_intent("age_limit") == "You must be 18."


def test_intents_without_a_reviewed_answer_get_the_provisional_one(
    tmp_path: Path,
) -> None:
    answers = CannedAnswers.from_dir(tmp_path)

    assert answers.for_intent("country_support") == PROVISIONAL_ANSWER


def test_missing_directory_means_no_answers_yet(tmp_path: Path) -> None:
    # Antes del S6 policies/canned/ no existe: no es un error.
    answers = CannedAnswers.from_dir(tmp_path / "policies" / "canned")

    assert len(answers) == 0


def test_call_template_warns_about_vishing() -> None:
    # El cliente va a recibir una llamada "del banco": el fraude más común.
    assert "never ask for your PIN" in CALL_TEMPLATE
    assert "as soon as possible" in CALL_TEMPLATE  # sin plazos que comprometan
