"""Dobles de prueba compartidos por los tests (importables: pythonpath = tests)."""

from typing import ClassVar


class FakeClassifier:
    """Clasificador para los tests: responde lo que el test le diga.

    Sin el modelo real (los tests no dependen de mise run ml:train) y sin que
    un reentrenamiento cambie el resultado de un test. Por defecto duda entre
    dos intenciones de IA → el router lo manda a persona ("low confidence").
    """

    version = "fake-classifier"
    DEFAULT: ClassVar[dict[str, float]] = {
        "card_arrival": 0.5,
        "card_delivery_estimate": 0.5,
    }

    def __init__(self, classes: frozenset[str]) -> None:
        self.classes = classes
        self.predictions: dict[str, dict[str, float]] = {}
        self.error: Exception | None = None

    def answer(self, text: str, intent: str, confidence: float = 0.97) -> None:
        """Hace que `text` se clasifique como `intent` con esa confianza.

        El resto de la probabilidad va a una intención de IA cualquiera, para
        que P(persona) no cuente salvo que la intención sea de persona.
        """
        filler = "age_limit" if intent != "age_limit" else "card_arrival"
        self.predictions[text] = {intent: confidence, filler: 1 - confidence}

    def predict(self, text: str) -> dict[str, float]:
        if self.error is not None:
            raise self.error
        return self.predictions.get(text, self.DEFAULT)
