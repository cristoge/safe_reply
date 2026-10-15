"""Exploración de Banking77 (paso 1 del plan).

Qué mira:
1. Tamaño y reparto de ejemplos por intención.
2. Longitud de los mensajes.
3. Duplicados: dentro del train, entre train y test, y el mismo texto con etiquetas distintas.
4. Intenciones que se confunden entre sí (predicciones con validación cruzada sobre el train).
5. Posibles etiquetas mal puestas: el modelo está muy seguro de otra intención.

Uso (desde la raíz del repo):
    uv run python ml/explore_banking77.py                     # todo
    uv run python ml/explore_banking77.py --intent card_arrival   # ejemplos de una intención
"""

import argparse

import numpy as np
import pandas as pd
from common import SEED, build_model, load_data
from sklearn.model_selection import StratifiedKFold, cross_val_predict


def section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def show_distribution(train: pd.DataFrame, test: pd.DataFrame) -> None:
    section("1. Reparto de ejemplos por intención")
    counts = train["category"].value_counts()
    print(f"Train: {len(train)} · Test: {len(test)} · Intenciones: {len(counts)}")
    print(f"Ejemplos por intención (train): mín {counts.min()} · media {counts.mean():.0f} · máx {counts.max()}")
    print(f"Test por intención: {test['category'].value_counts().unique()} (equilibrado)")
    print("\nLas 5 con más ejemplos:")
    print(counts.head(5).to_string())
    print("\nLas 5 con menos ejemplos:")
    print(counts.tail(5).to_string())


def show_lengths(train: pd.DataFrame) -> None:
    section("2. Longitud de los mensajes (palabras)")
    words = train["text"].str.split().str.len()
    print(words.describe().round(1).to_string())
    print(f"\nMás cortos:\n{train.loc[words.nsmallest(5).index, ['text', 'category']].to_string()}")
    print(f"\nMás largo:\n{train.loc[words.idxmax(), 'text']}")


def show_duplicates(train: pd.DataFrame, test: pd.DataFrame) -> None:
    section("3. Duplicados")
    # Comparar en minúsculas y sin espacios de sobra: "Card arrival?" == "card arrival?"
    norm_train = train["text"].str.lower().str.strip()
    norm_test = test["text"].str.lower().str.strip()

    print(f"Repetidos dentro del train: {norm_train.duplicated().sum()}")
    print(f"Frases del test que también están en el train: {norm_test.isin(set(norm_train)).sum()}")

    # El mismo texto con dos etiquetas distintas = ruido seguro
    labels_per_text = train.assign(norm=norm_train).groupby("norm")["category"].nunique()
    conflicts = labels_per_text[labels_per_text > 1].index
    print(f"Mismo texto con etiquetas distintas (train): {len(conflicts)}")
    for text in conflicts[:10]:
        cats = train.loc[norm_train == text, "category"].unique()
        print(f"  {text!r} → {', '.join(cats)}")


def cross_val_probabilities(train: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Probabilidades de cada ejemplo del train predichas por un modelo que NO lo ha visto."""
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    proba = cross_val_predict(build_model(), train["text"], train["category"], cv=cv, method="predict_proba")
    classes = np.sort(train["category"].unique())  # el orden que usa scikit-learn
    return proba, classes


def show_confusions(train: pd.DataFrame, proba: np.ndarray, classes: np.ndarray) -> None:
    section("4. Intenciones que más se confunden (validación cruzada en el train)")
    pred = classes[proba.argmax(axis=1)]
    errors = train.assign(pred=pred).query("category != pred")
    print(f"Acierto en validación cruzada: {1 - len(errors) / len(train):.3f}\n")

    # Contar el par sin importar el sentido: A→B y B→A son la misma confusión
    pairs = errors.apply(lambda r: " ↔ ".join(sorted([r["category"], r["pred"]])), axis=1)
    print("Los 15 pares más confundidos (errores en los dos sentidos):")
    print(pairs.value_counts().head(15).to_string())


def show_suspicious_labels(train: pd.DataFrame, proba: np.ndarray, classes: np.ndarray) -> None:
    section("5. Posibles etiquetas mal puestas")
    print("El modelo (sin haber visto el ejemplo) está muy seguro de otra intención")
    print("y casi descarta la etiqueta original. No todos son errores: revisar a mano.\n")
    class_index = {c: i for i, c in enumerate(classes)}
    label_proba = proba[np.arange(len(train)), train["category"].map(class_index)]
    df = train.assign(
        pred=classes[proba.argmax(axis=1)],
        pred_proba=proba.max(axis=1),
        label_proba=label_proba,
    )
    suspicious = df.query("pred != category and pred_proba > 0.7 and label_proba < 0.05")
    print(f"Candidatos: {len(suspicious)} de {len(train)} ({len(suspicious) / len(train):.1%})\n")
    for _, r in suspicious.sort_values("pred_proba", ascending=False).head(15).iterrows():
        print(f"  {r['text']}")
        print(f"      etiqueta: {r['category']} ({r['label_proba']:.2f}) · modelo: {r['pred']} ({r['pred_proba']:.2f})")


def show_intent(train: pd.DataFrame, intent: str, n: int = 15) -> None:
    rows = train[train["category"] == intent]
    if rows.empty:
        print(f"No existe la intención {intent!r}. Algunas: {', '.join(train['category'].unique()[:5])}...")
        return
    section(f"{intent} · {len(rows)} ejemplos en el train")
    for text in rows["text"].sample(min(n, len(rows)), random_state=SEED):
        print(f"  - {text}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--intent", help="muestra ejemplos de esta intención y termina")
    args = parser.parse_args()

    train, test = load_data()
    if args.intent:
        show_intent(train, args.intent)
        return

    show_distribution(train, test)
    show_lengths(train)
    show_duplicates(train, test)
    print("\n(Entrenando 5 modelos para la validación cruzada...)")
    proba, classes = cross_val_probabilities(train)
    show_confusions(train, proba, classes)
    show_suspicious_labels(train, proba, classes)


if __name__ == "__main__":
    main()
