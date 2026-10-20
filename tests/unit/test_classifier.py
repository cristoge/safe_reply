"""JoblibClassifier y load_triage, con un modelo diminuto entrenado en el test.

No usan models/intent_classifier.joblib: los tests no dependen de haber
ejecutado mise run ml:train.
"""

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import joblib
import pytest
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

from safe_reply.clients.classifier import ClassifierLoadError, JoblibClassifier
from safe_reply.core.config import get_settings
from safe_reply.main import load_triage
from safe_reply.triage.answers import CannedAnswersError
from safe_reply.triage.rules import Rules

CANNED_DIR = Path(__file__).parents[2] / "policies" / "canned"


def save_model(
    path: Path,
    texts: list[str],
    labels: list[str],
    *,
    sklearn_version: str = sklearn.__version__,
) -> Path:
    model = make_pipeline(TfidfVectorizer(), LogisticRegression())
    model.fit(texts, labels)
    joblib.dump(model, path)
    metadata = {"version": "tiny-v1", "sklearn_version": sklearn_version}
    path.with_suffix(".json").write_text(json.dumps(metadata), encoding="utf-8")
    return path


@pytest.fixture
def tiny_model(tmp_path: Path) -> Path:
    return save_model(
        tmp_path / "model.joblib",
        [
            "how old must I be",
            "minimum age",
            "card was stolen",
            "someone stole my card",
        ],
        ["age_limit", "age_limit", "lost_or_stolen_card", "lost_or_stolen_card"],
    )


def test_loads_and_predicts_a_probability_per_intent(tiny_model: Path) -> None:
    classifier = JoblibClassifier.load(tiny_model)

    probabilities = classifier.predict("my card was stolen")

    assert classifier.version == "tiny-v1"
    assert classifier.classes == {"age_limit", "lost_or_stolen_card"}
    assert set(probabilities) == classifier.classes
    assert sum(probabilities.values()) == pytest.approx(1)
    assert max(probabilities, key=probabilities.__getitem__) == "lost_or_stolen_card"
    assert all(type(p) is float for p in probabilities.values())  # no numpy


def test_missing_model_says_how_to_create_it(tmp_path: Path) -> None:
    with pytest.raises(ClassifierLoadError, match="mise run ml:train"):
        JoblibClassifier.load(tmp_path / "missing.joblib")


def test_model_saved_with_another_sklearn_does_not_load(tmp_path: Path) -> None:
    path = save_model(
        tmp_path / "model.joblib",
        ["alpha", "beta"],
        ["x", "y"],
        sklearn_version="0.24.2",
    )

    with pytest.raises(ClassifierLoadError, match="0.24.2"):
        JoblibClassifier.load(path)


# Un ejemplo por intención: scikit-learn avisa de que parece una regresión.
@pytest.mark.filterwarnings("ignore:The number of unique classes")
def test_load_triage_with_a_model_for_every_intent(
    tmp_path: Path, rules: Rules
) -> None:
    intents = sorted(rules.intents)
    path = save_model(tmp_path / "model.joblib", intents, intents)
    settings = get_settings().model_copy(
        update={"classifier_path": path, "canned_dir": CANNED_DIR}
    )

    loaded_rules, classifier, answers = load_triage(settings)

    assert classifier.classes == set(loaded_rules.intents)
    assert len(answers) == 57  # 29 canned + 28 rag: las intenciones de IA


@pytest.mark.filterwarnings("ignore:The number of unique classes")
@pytest.mark.parametrize(
    ("change", "message"),
    [
        # Falta una intención de IA: no hay texto de reserva.
        (lambda d: (d / "age_limit.md").unlink(), r"faltan: \['age_limit'\]"),
        # Sobra una de persona: nunca se debe enviar sin una persona.
        (
            lambda d: (d / "lost_or_stolen_card.md").write_text("x"),
            r"sobran: \['lost_or_stolen_card'\]",
        ),
    ],
)
def test_load_triage_refuses_answers_that_do_not_match_the_rules(
    tmp_path: Path,
    rules: Rules,
    change: Callable[[Path], object],
    message: str,
) -> None:
    intents = sorted(rules.intents)
    path = save_model(tmp_path / "model.joblib", intents, intents)
    canned_dir = tmp_path / "canned"
    shutil.copytree(CANNED_DIR, canned_dir)
    change(canned_dir)
    settings = get_settings().model_copy(
        update={"classifier_path": path, "canned_dir": canned_dir}
    )

    with pytest.raises(CannedAnswersError, match=message):
        load_triage(settings)


def test_load_triage_refuses_a_model_that_does_not_match_the_rules(
    tiny_model: Path,
) -> None:
    settings = get_settings().model_copy(update={"classifier_path": tiny_model})

    # El modelo solo conoce 2 intenciones: las otras 75 del YAML nunca saldrían.
    with pytest.raises(ClassifierLoadError, match="solo en el YAML"):
        load_triage(settings)
