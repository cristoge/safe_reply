"""Evalúa el modelo definitivo para lo que importa: tipo, prioridad y ruta (IA o persona).

Acertar la intención exacta no es el objetivo: confundir card_arrival con
card_delivery_estimate no cambia nada (mismo tipo, misma ruta). Lo grave es que
un ticket que debía ir a persona acabe respondido por la IA.

Secciones:
1. Acierto por nivel: intención, tipo y ruta.
2. Umbral × P(persona): cuántos se cuelan en la IA y cuántas llamadas (bloque 1.1).
3. Respuestas predefinidas: cuántos tickets responden y cuántos con la intención mal (1.2).
4. Con la configuración de rules/intents.yaml (incluida la red de seguridad de palabras
   sensibles): qué se cuela y qué falla.

La sección 2 no usa la red de seguridad, para medir solo el efecto de P(persona).

Uso (desde la raíz del repo): uv run python ml/compare_models.py
"""

import numpy as np
import pandas as pd
from common import (
    CANNED,
    HUMAN,
    INTENT_ROUTE,
    INTENT_TYPE,
    RAG,
    ROUTER,
    RULES,
    build_model,
    load_data,
    route_tickets,
)
from sklearn.metrics import accuracy_score, classification_report, f1_score

THRESHOLDS = [0.5, 0.6, 0.7, 0.8, 0.9]
P_HUMAN_LIMITS = [None, 0.3, 0.2, 0.1, 0.05]
CANNED_THRESHOLDS = [None, 0.8, 0.85, 0.9, 0.95]


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def summarize(destination: np.ndarray, pred: np.ndarray, y: np.ndarray) -> dict:
    """Las cifras del router: dónde acaba cada ticket y cuántos errores llegan al cliente."""
    true_human = np.array([INTENT_ROUTE[i] == HUMAN for i in y])
    to_ai = destination != HUMAN
    canned = destination == CANNED
    return {
        "IA responde": f"{to_ai.mean():.1%}",
        "predefinidas": f"{canned.mean():.1%}",
        "llamadas": f"{1 - to_ai.mean():.1%}",
        "se cuelan": int((to_ai & true_human).sum()),
        "IA con intención mal": int((to_ai & (pred != y)).sum()),
        "predefinida mal": int((canned & (pred != y)).sum()),
    }


def show_levels(pred: np.ndarray, y: np.ndarray) -> None:
    section("1. Acierto por nivel")
    to_type, to_route = np.vectorize(INTENT_TYPE.get), np.vectorize(INTENT_ROUTE.get)
    print(
        f"Intención exacta (77): accuracy {accuracy_score(y, pred):.3f} · F1 macro {f1_score(y, pred, average='macro'):.3f}"
    )
    print(
        f"Tipo (10):             accuracy {accuracy_score(to_type(y), to_type(pred)):.3f}"
    )
    print(
        f"Ruta (IA o persona):   accuracy {accuracy_score(to_route(y), to_route(pred)):.3f}"
    )


def show_p_human(proba: np.ndarray, classes: np.ndarray, y: np.ndarray) -> None:
    section("2. Umbral × P(persona) (sin predefinidas: todo lo de IA va al RAG)")
    n_human = sum(INTENT_ROUTE[i] == HUMAN for i in y)
    print(f"Tickets que deben ir a persona en el test: {n_human} de {len(y)}\n")
    rows = []
    for threshold in THRESHOLDS:
        for limit in P_HUMAN_LIMITS:
            pred, dest = route_tickets(proba, classes, threshold, limit)
            row = summarize(dest, pred, y)
            rows.append(
                {"umbral": threshold, "límite P(persona)": limit or "—"}
                | {
                    k: row[k]
                    for k in (
                        "IA responde",
                        "llamadas",
                        "se cuelan",
                        "IA con intención mal",
                    )
                }
            )
    print(pd.DataFrame(rows).to_string(index=False))


def show_canned(
    proba: np.ndarray, classes: np.ndarray, y: np.ndarray, texts: list[str]
) -> None:
    threshold, limit = ROUTER.min_confidence, ROUTER.max_p_human
    section(
        f"3. Respuestas predefinidas (umbral {threshold}, límite P(persona) {limit})"
    )
    canned_intents = sorted(i for i, r in RULES.items() if r.answer_mode == CANNED)
    in_test = np.isin(y, canned_intents).mean()
    print(
        f"Intenciones con answer_mode: canned: {len(canned_intents)} de 77 · {in_test:.1%} del test\n"
    )
    rows = []
    for canned_min in CANNED_THRESHOLDS:
        pred, dest = route_tickets(proba, classes, threshold, limit, canned_min, texts)
        rows.append(
            {"confianza mín. predefinida": canned_min or "sin predefinidas"}
            | summarize(dest, pred, y)
        )
    print(pd.DataFrame(rows).to_string(index=False))


def show_current_config(
    proba: np.ndarray, classes: np.ndarray, y: np.ndarray, texts: list[str]
) -> None:
    config = (
        ROUTER.min_confidence,
        ROUTER.max_p_human,
        ROUTER.canned_min_confidence,
    )
    section(
        f"4. Configuración de rules/intents.yaml (umbral, límite, predefinidas) = {config}"
    )
    pred, dest = route_tickets(proba, classes, *config, texts=texts)
    for name, value in summarize(dest, pred, y).items():
        print(f"  {name:<22} {value}")
    print(f"  {'RAG':<22} {(dest == RAG).mean():.1%}")

    true_human = np.array([INTENT_ROUTE[i] == HUMAN for i in y])
    leaked = (dest != HUMAN) & true_human
    print(f"\nSe cuelan en la IA ({leaked.sum()}):")
    for i in np.where(leaked)[0]:
        print(f"  [{y[i]} → {pred[i]} · {dest[i]}] {texts[i]}")

    wrong_canned = (dest == CANNED) & (pred != y)
    print(
        f"\nPredefinidas con la intención mal ({wrong_canned.sum()}), por intención predicha:"
    )
    pairs = pd.Series(
        [f"{p} ← {real}" for p, real in zip(pred[wrong_canned], y[wrong_canned])]
    )
    print(pairs.value_counts().to_string() if len(pairs) else "  ninguna")

    canned = dest == CANNED
    per_intent = pd.DataFrame(
        {"pred": pred[canned], "ok": pred[canned] == y[canned]}
    ).groupby("pred")["ok"]
    table = pd.DataFrame(
        {"respondidas": per_intent.size(), "mal": per_intent.size() - per_intent.sum()}
    )
    print("\nPredefinidas por intención (de 40 tickets de cada una en el test):")
    print(table.sort_values("mal", ascending=False).to_string())


def show_worst(pred: np.ndarray, y: np.ndarray) -> None:
    section("5. Las 10 intenciones con peor F1")
    # zero_division=0 es válido (lo admite la documentación); sus tipos dicen que solo str
    metrics = classification_report(y, pred, output_dict=True, zero_division=0)  # pyright: ignore[reportArgumentType]
    report = pd.DataFrame(metrics).T
    worst = (
        report.drop(["accuracy", "macro avg", "weighted avg"])
        .sort_values("f1-score")
        .head(10)
    )
    worst["ruta"] = worst.index.map(INTENT_ROUTE)
    print(worst[["precision", "recall", "f1-score", "ruta"]].round(2).to_string())


def main() -> None:
    train, test = load_data()
    missing = set(train["category"]) ^ set(RULES)
    assert not missing, (
        f"Las reglas no cubren exactamente las 77 intenciones: {missing}"
    )

    model = build_model().fit(train["text"], train["category"])
    proba = model.predict_proba(test["text"])
    classes = model.classes_
    y = test["category"].to_numpy()
    pred = classes[proba.argmax(axis=1)]

    print(f"Train: {len(train)} · Test: {len(test)} · Intenciones: {len(classes)}")
    show_levels(pred, y)
    show_p_human(proba, classes, y)
    texts = list(test["text"])
    show_canned(proba, classes, y, texts)
    show_current_config(proba, classes, y, texts)
    show_worst(pred, y)


if __name__ == "__main__":
    main()
