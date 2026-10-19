"""Entrena el clasificador definitivo y lo guarda para el backend.

Entrena con todo el train de Banking77 (el test solo se usa para las métricas
de los metadatos) y escribe en models/ (fuera de Git: se regenera en segundos):
    intent_classifier.joblib   el modelo (TF-IDF + regresión logística)
    intent_classifier.json     sus metadatos: versión, scikit-learn, datos, métricas

Uso (desde la raíz del repo, con los datos descargados: mise run ml:data):
    uv run python ml/train.py        o        mise run ml:train
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import joblib
import sklearn
from common import DATA_DIR, ML_DIR, build_model, load_data
from sklearn.metrics import accuracy_score, f1_score

MODELS_DIR = ML_DIR.parent / "models"
MODEL_PATH = MODELS_DIR / "intent_classifier.joblib"
METADATA_PATH = MODELS_DIR / "intent_classifier.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    train, test = load_data()
    model = build_model().fit(train["text"], train["category"])
    pred = model.predict(test["text"])

    trained_at = datetime.now(UTC)
    train_hash = sha256(DATA_DIR / "train.csv")
    metadata = {
        # Lo que se guarda en tickets.classifier_version (≤ 50 caracteres): con
        # la fecha y los datos, dos modelos distintos nunca comparten versión.
        "version": f"tfidf-lr-{trained_at:%Y%m%d}-{train_hash[:8]}",
        "algorithm": "TF-IDF (unigramas y bigramas) + regresión logística",
        # Un .joblib solo se puede cargar con la misma versión de scikit-learn
        # con la que se guardó: el backend lo comprueba al arrancar.
        "sklearn_version": sklearn.__version__,
        "trained_at": trained_at.isoformat(timespec="seconds"),
        "train_sha256": train_hash,
        "train_examples": len(train),
        "classes": len(model.classes_),
        "test_metrics": {
            "accuracy": round(accuracy_score(test["category"], pred), 4),
            "f1_macro": round(f1_score(test["category"], pred, average="macro"), 4),
        },
    }

    MODELS_DIR.mkdir(exist_ok=True)
    # compress=3: 13.5 MB en vez de 15 y se sigue cargando en ~0.06 s.
    joblib.dump(model, MODEL_PATH, compress=3)
    METADATA_PATH.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    size_mb = MODEL_PATH.stat().st_size / 1e6
    print(
        f"Modelo {metadata['version']} → {MODEL_PATH.relative_to(ML_DIR.parent)} ({size_mb:.1f} MB)"
    )
    print(
        f"Test: accuracy {metadata['test_metrics']['accuracy']} · F1 macro {metadata['test_metrics']['f1_macro']}"
    )


if __name__ == "__main__":
    main()
