"""Lo que comparten los scripts de ml/: datos, modelo definitivo, reglas y router."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, make_pipeline

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


# --- Reglas de negocio (rules/intents.yaml) ---------------------------------


@dataclass(frozen=True)
class IntentRule:
    ticket_type: str
    priority: str
    route: str  # ai | human
    answer_mode: str | None  # rag | canned (solo con route: ai)
    min_confidence: float | None
    reason: str | None


def load_rules(
    path: Path = RULES_PATH,
) -> tuple[dict[str, IntentRule], dict, re.Pattern[str]]:
    """Devuelve (reglas por intención, configuración del router, red de seguridad)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules = {
        intent: IntentRule(
            ticket_type=r["ticket_type"],
            priority=r["priority"],
            route=r["route"],
            answer_mode=r.get("answer_mode"),
            min_confidence=r.get("min_confidence"),
            reason=r.get("reason"),
        )
        for intent, r in data["intents"].items()
    }
    sensitive = re.compile("|".join(data["sensitive_patterns"]), re.IGNORECASE)
    return rules, data["router"], sensitive


RULES, ROUTER, SENSITIVE = load_rules()
INTENT_TYPE = {intent: rule.ticket_type for intent, rule in RULES.items()}
INTENT_ROUTE = {intent: rule.route for intent, rule in RULES.items()}


# --- Router (reglas 0 a 4 del apartado 6 del diseño) ------------------------

HUMAN, CANNED, RAG = "human", "canned", "rag"


def decide_routes(
    proba: np.ndarray,
    classes: np.ndarray,
    min_confidence: float,
    max_p_human: float | None = None,
    canned_min_confidence: float | None = None,
    texts: Sequence[str] | None = None,
    rules: dict[str, IntentRule] = RULES,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decide a dónde va cada ticket a partir de las probabilidades del modelo.

    Las reglas se miran en orden; la primera que se cumple da el motivo (route_reason):
    0. El texto contiene una palabra sensible (si se pasan `texts`) → persona
    1. La intención predicha es de "siempre persona"          → persona
    2. Confianza < umbral (o < min_confidence de la intención) → persona
    3. P(persona) ≥ max_p_human (si se pasa)                  → persona
    4. answer_mode canned y confianza ≥ canned_min_confidence → predefinida
    5. Si no                                                  → RAG

    `texts=None` desactiva la regla 0 · `max_p_human=None`, la 3 ·
    `canned_min_confidence=None`, la 4 (todo lo de IA iría al RAG).
    Devuelve (intención predicha, destino, motivo).
    """
    n = len(proba)
    pred = classes[proba.argmax(axis=1)]
    confidence = proba.max(axis=1)
    human_columns = np.array([rules[c].route == "human" for c in classes])
    p_human = proba[:, human_columns].sum(axis=1)

    rule = [rules[p] for p in pred]
    intent_min = np.array([max(min_confidence, r.min_confidence or 0) for r in rule])
    sensitive = (
        np.array([SENSITIVE.search(t) is not None for t in texts])
        if texts is not None
        else np.zeros(n, dtype=bool)
    )
    ambiguous = (
        p_human >= max_p_human if max_p_human is not None else np.zeros(n, dtype=bool)
    )
    reason = np.select(
        [
            sensitive,
            np.array([r.route == "human" for r in rule]),
            confidence < intent_min,
            ambiguous,
        ],
        [
            "sensitive keyword",
            "intent always human",
            "low confidence",
            "ambiguous with sensitive intent",
        ],
        default="",
    ).astype(object)
    # Intención de persona, pero el modelo duda: el motivo lo dice (el destino es el mismo)
    reason[(reason == "intent always human") & (confidence < intent_min)] = (
        "intent always human · low confidence"
    )
    allowed = reason == ""

    destination = np.where(allowed, RAG, HUMAN).astype(object)
    if canned_min_confidence is not None:
        canned_min = np.maximum(intent_min, canned_min_confidence)
        is_canned = np.array([r.answer_mode == "canned" for r in rule])
        canned = allowed & is_canned & (confidence >= canned_min)
        destination[canned] = CANNED
        reason[canned] = "canned answer for FAQ intent"
    return pred, destination, reason


def route_tickets(
    proba: np.ndarray,
    classes: np.ndarray,
    min_confidence: float,
    max_p_human: float | None = None,
    canned_min_confidence: float | None = None,
    texts: Sequence[str] | None = None,
    rules: dict[str, IntentRule] = RULES,
) -> tuple[np.ndarray, np.ndarray]:
    """Como `decide_routes`, sin el motivo: (intención predicha, destino)."""
    pred, destination, _ = decide_routes(
        proba, classes, min_confidence, max_p_human, canned_min_confidence, texts, rules
    )
    return pred, destination
