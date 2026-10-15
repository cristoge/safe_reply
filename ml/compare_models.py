"""Evalúa el modelo definitivo para lo que importa: tipo, prioridad y ruta (IA o persona).

Acertar la intención exacta no es el objetivo: confundir card_arrival con
card_delivery_estimate no cambia nada (mismo tipo, misma ruta). Lo grave es que
un ticket que debía ir a persona acabe respondido por la IA.

Uso (desde la raíz del repo): uv run python ml/compare_models.py
"""

import pandas as pd
from common import INTENT_ROUTE, INTENT_TYPE, build_model, load_data
from sklearn.metrics import accuracy_score, classification_report, f1_score

THRESHOLDS = [0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]


def main() -> None:
    train, test = load_data()
    missing = set(train["category"]) ^ set(INTENT_TYPE)
    assert not missing, f"Las reglas no cubren exactamente las 77 intenciones: {missing}"

    model = build_model().fit(train["text"], train["category"])
    proba = model.predict_proba(test["text"])
    pred = model.classes_[proba.argmax(axis=1)]
    confidence = proba.max(axis=1)
    y = test["category"].to_numpy()

    true_type, pred_type = test["category"].map(INTENT_TYPE), pd.Series(pred).map(INTENT_TYPE)
    true_route = test["category"].map(INTENT_ROUTE).to_numpy()
    pred_route = pd.Series(pred).map(INTENT_ROUTE).to_numpy()

    print(f"Train: {len(train)} · Test: {len(test)} · Intenciones: {train['category'].nunique()}\n")
    print(f"Intención exacta (77): accuracy {accuracy_score(y, pred):.3f} · F1 macro {f1_score(y, pred, average='macro'):.3f}")
    print(f"Tipo (10):             accuracy {accuracy_score(true_type, pred_type):.3f}")
    print(f"Ruta (IA o persona):   accuracy {accuracy_score(true_route, pred_route):.3f}")

    # Router: la IA responde si la regla lo permite Y la confianza llega al umbral
    n_human = (true_route == "human").sum()
    rows = []
    for threshold in THRESHOLDS:
        to_ai = (pred_route == "ai") & (confidence >= threshold)
        rows.append({
            "umbral": threshold,
            "IA responde": f"{to_ai.mean():.1%}",
            "llamadas": f"{1 - to_ai.mean():.1%}",
            "IA con intención mal": (to_ai & (pred != y)).sum(),
            "debían ir a persona": (to_ai & (true_route == "human")).sum(),
        })
    print(f"\nUmbral de confianza ({n_human} tickets de persona en el test):")
    print(pd.DataFrame(rows).to_string(index=False))

    # Los errores peligrosos (sin umbral): debía ir a persona y lo responde la IA
    danger = (pred_route == "ai") & (true_route == "human")
    pairs = pd.Series([f"{real} → {p}" for real, p in zip(y[danger], pred[danger])])
    print("\nSe cuelan en la IA (real → predicha), sin umbral:")
    print(pairs.value_counts().head(10).to_string())

    report = pd.DataFrame(classification_report(y, pred, output_dict=True, zero_division=0)).T
    worst = report.drop(["accuracy", "macro avg", "weighted avg"]).sort_values("f1-score").head(10)
    worst["ruta"] = worst.index.map(INTENT_ROUTE)
    print("\nLas 10 intenciones con peor F1:")
    print(worst[["precision", "recall", "f1-score", "ruta"]].to_string(float_format=lambda x: f"{x:.2f}"))


if __name__ == "__main__":
    main()
