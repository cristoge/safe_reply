"""El clasificador de intención: un puerto (Protocol) y el adaptador con el modelo de ml/.

El procesado solo conoce IntentClassifier: en los tests se usa uno falso, y
cambiar de modelo (un transformer, un LLM) es otro adaptador sin tocar nada más.
"""

import json
from pathlib import Path
from typing import Any, Protocol

import joblib
import sklearn


class ClassifierLoadError(Exception):
    """El modelo no se puede usar: la app no debe arrancar sin él (fail fast)."""


class IntentClassifier(Protocol):
    # Lo que se guarda en tickets.classifier_version: qué modelo decidió.
    version: str
    classes: frozenset[str]

    def predict(self, text: str) -> dict[str, float]:
        """La probabilidad de cada intención (suman 1)."""
        ...


class JoblibClassifier:
    """El modelo que guarda ml/train.py (TF-IDF + regresión logística)."""

    def __init__(self, model: Any, version: str) -> None:
        self._model = model
        self.version = version
        self.classes = frozenset(str(c) for c in model.classes_)

    @classmethod
    def load(cls, model_path: Path) -> JoblibClassifier:
        """Carga el .joblib y comprueba sus metadatos (el .json de al lado)."""
        metadata_path = model_path.with_suffix(".json")
        if not model_path.is_file() or not metadata_path.is_file():
            msg = f"No existe {model_path} (o su .json): ejecuta `mise run ml:train`"
            raise ClassifierLoadError(msg)

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        # Un .joblib es un pickle: con otra versión de scikit-learn puede cargar
        # y predecir mal sin avisar. Mejor no arrancar.
        if metadata["sklearn_version"] != sklearn.__version__:
            msg = (
                f"{model_path} se guardó con scikit-learn {metadata['sklearn_version']}"
                f" y está instalada la {sklearn.__version__}: ejecuta `mise run ml:train`"
            )
            raise ClassifierLoadError(msg)
        # joblib.load ejecuta el pickle: solo archivos generados por nosotros
        # (models/ no viene de fuera ni lo sube nadie).
        return cls(joblib.load(model_path), metadata["version"])

    def predict(self, text: str) -> dict[str, float]:
        # ~0.5 ms por ticket en CPU: no compensa sacarlo del event loop.
        probabilities = self._model.predict_proba([text])[0]
        return {
            str(intent): float(p)
            for intent, p in zip(self._model.classes_, probabilities, strict=True)
        }
