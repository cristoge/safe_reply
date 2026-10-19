"""Lo que comparten los scripts de ml/: datos, modelo definitivo, reglas y router.

Las reglas y el router no se definen aquí: son los del backend (revision_rag.triage).
"""

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, make_pipeline

from revision_rag.triage.router import Destination, decide_route, summarize
from revision_rag.triage.rules import IntentRule, RouterConfig, Rules, load_rules

ML_DIR = Path(__file__).parent
DATA_DIR = ML_DIR / "data" / "banking77"
PHRASES_DIR = ML_DIR / "phrases"
RULES_PATH = ML_DIR.parent / "rules" / "intents.yaml"
SEED = 42


def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    train = pd.read_csv(DATA_DIR / "train.csv")
    test = pd.read_csv(DATA_DIR / "test.csv")
    return train, test


def load_phrases(name: str) -> list[str]:
    """Frases de prueba escritas a mano (ml/phrases/<name>.txt): una por línea, # = comentario."""
    lines = (PHRASES_DIR / f"{name}.txt").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def build_model() -> Pipeline:
    """Modelo definitivo: TF-IDF + regresión logística.

    Elegido frente a Naive Bayes, SVM (normal y calibrado), SGD, KNN y Random Forest:
    mejor F1 macro (0.898, empatado con los demás lineales) y da probabilidades
    sin calibrar aparte → sirven para el umbral de confianza.
    """
    return make_pipeline(
        # sublinear_tf: una palabra repetida no pesa el doble · bigramas: "not working", "top up"
        TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True),
        LogisticRegression(C=20, class_weight="balanced", max_iter=2000),
    )


# --- Reglas de negocio y router: los del backend (src/revision_rag/triage) ----
# No hay una copia aquí: lo que se mide es exactamente lo que responde a los
# clientes. Si cambia una regla, cambia para los dos.

RULES_CONFIG: Rules = load_rules(RULES_PATH)
RULES: dict[str, IntentRule] = RULES_CONFIG.intents
ROUTER: RouterConfig = RULES_CONFIG.router
SENSITIVE = RULES_CONFIG.sensitive
INTENT_TYPE = {intent: rule.ticket_type for intent, rule in RULES.items()}
INTENT_ROUTE = {intent: rule.route for intent, rule in RULES.items()}

HUMAN, CANNED, RAG = Destination.HUMAN, Destination.CANNED, Destination.RAG


def decide_routes(
    proba: np.ndarray,
    classes: np.ndarray,
    min_confidence: float,
    max_p_human: float | None = None,
    canned_min_confidence: float | None = None,
    texts: Sequence[str] | None = None,
    rules: Rules = RULES_CONFIG,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pasa cada ticket por triage.router.decide_route con estos umbrales.

    `texts=None` desactiva la regla 0 · `max_p_human=None`, la 3 ·
    `canned_min_confidence=None`, la 4 (todo lo de IA iría al RAG).
    Devuelve (intención predicha, destino, motivo); el motivo del RAG es "".
    """
    config = RouterConfig(
        min_confidence=min_confidence,
        max_p_human=max_p_human,
        canned_min_confidence=canned_min_confidence,
    )
    pred, destination, reason = [], [], []
    for i, row in enumerate(proba):
        prediction = summarize(dict(zip(classes, row, strict=True)), rules)
        text = texts[i] if texts is not None else None
        decision = decide_route(prediction, text, rules, config)
        pred.append(prediction.intent)
        destination.append(decision.destination)
        reason.append(decision.reason or "")
    return (
        np.array(pred),
        np.array(destination, dtype=object),
        np.array(reason, dtype=object),
    )


def route_tickets(
    proba: np.ndarray,
    classes: np.ndarray,
    min_confidence: float,
    max_p_human: float | None = None,
    canned_min_confidence: float | None = None,
    texts: Sequence[str] | None = None,
    rules: Rules = RULES_CONFIG,
) -> tuple[np.ndarray, np.ndarray]:
    """Como `decide_routes`, sin el motivo: (intención predicha, destino)."""
    pred, destination, _ = decide_routes(
        proba, classes, min_confidence, max_p_human, canned_min_confidence, texts, rules
    )
    return pred, destination
