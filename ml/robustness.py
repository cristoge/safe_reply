"""¿Qué hace el router con tickets que no se parecen a Banking77? (bloques 1.3 y 1.4)

1. Fuera de ámbito: frases propias que no son de banca → no deberían ir a la IA.
2. Intenciones escondidas: entrenar sin algunas intenciones y usar sus frases del test
   como "desconocidas". Siguen siendo de banca → más difícil que el fuera de ámbito.
3. Tickets con dos problemas (uno de "siempre persona") → deberían ir a persona.
4. Tickets largos y realistas → ¿acierta la intención y la ruta?

Las frases están en ml/phrases/. Usa el router completo de rules/intents.yaml, incluida
la red de seguridad de palabras sensibles.

Uso (desde la raíz del repo): uv run python ml/robustness.py
"""

import numpy as np
import pandas as pd
from common import (
    HUMAN,
    INTENT_ROUTE,
    ROUTER,
    RULES,
    SEED,
    build_model,
    load_data,
    load_phrases,
    route_tickets,
)
from sklearn.pipeline import Pipeline

# Umbrales a comparar en las secciones 1 y 2 (el límite de P(persona) y las predefinidas, los del YAML)
THRESHOLDS = [0.5, 0.6, 0.7, 0.75, 0.8, 0.9]
HIDDEN_INTENTS = 10
HIDDEN_REPEATS = 5


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def route(
    model: Pipeline, texts: list[str] | pd.Series, threshold: float | None = None
) -> pd.DataFrame:
    """Pasa textos por el modelo y el router. Devuelve intención, confianza, P(persona) y destino."""
    proba = model.predict_proba(texts)
    pred, dest = route_tickets(
        proba,
        model.classes_,
        ROUTER.min_confidence if threshold is None else threshold,
        ROUTER.max_p_human,
        ROUTER.canned_min_confidence,
        list(texts),
    )
    human_columns = np.array([INTENT_ROUTE[c] == HUMAN for c in model.classes_])
    return pd.DataFrame(
        {
            "text": list(texts),
            "pred": pred,
            "confidence": proba.max(axis=1),
            "p_human": proba[:, human_columns].sum(axis=1),
            "dest": dest,
        }
    )


def show_rows(df: pd.DataFrame, columns: list[str]) -> None:
    for _, r in df.iterrows():
        extra = " · ".join(
            f"{c}={r[c]:.2f}" if isinstance(r[c], float) else f"{c}={r[c]}"
            for c in columns
        )
        print(f"  [{extra}] {r['text']}")


def show_out_of_scope(model: Pipeline) -> None:
    section("1. Fuera de ámbito (frases propias)")
    phrases = load_phrases("out_of_scope")
    rows = [
        {
            "umbral": t,
            "a la IA": int((route(model, phrases, t)["dest"] != HUMAN).sum()),
            "de": len(phrases),
        }
        for t in THRESHOLDS
    ]
    print(pd.DataFrame(rows).to_string(index=False))

    result = route(model, phrases)
    print(
        f"\nConfianza: mediana {result['confidence'].median():.2f} · máxima {result['confidence'].max():.2f}"
    )
    leaked = result.loc[result["dest"] != HUMAN]
    print(f"Con la configuración del YAML, van a la IA ({len(leaked)}):")
    if len(leaked):
        show_rows(leaked, ["pred", "confidence", "dest"])
    else:
        print("  ninguna")
    print("Las 5 con más confianza:")
    show_rows(result.nlargest(5, "confidence"), ["pred", "confidence", "dest"])


def show_hidden_intents(train: pd.DataFrame, test: pd.DataFrame) -> None:
    section(
        f"2. Intenciones escondidas ({HIDDEN_INTENTS} de 77, {HIDDEN_REPEATS} repeticiones)"
    )
    print(
        "El modelo nunca ha visto esas intenciones: cualquier respuesta de la IA sería incorrecta.\n"
    )
    rng = np.random.default_rng(SEED)
    intents = np.array(sorted(RULES))
    answered = {t: [] for t in THRESHOLDS}
    for _ in range(HIDDEN_REPEATS):
        hidden = list(rng.choice(intents, HIDDEN_INTENTS, replace=False))
        seen = train.loc[~train["category"].isin(hidden)]
        model = build_model().fit(seen["text"], seen["category"])
        unseen = test.loc[test["category"].isin(hidden), "text"].tolist()
        for t in THRESHOLDS:
            answered[t].append((route(model, unseen, t)["dest"] != HUMAN).mean())
    rows = [
        {
            "umbral": t,
            "la IA responde (media)": f"{np.mean(v):.1%}",
            "peor repetición": f"{max(v):.1%}",
        }
        for t, v in answered.items()
    ]
    print(pd.DataFrame(rows).to_string(index=False))


def show_multi_problem(model: Pipeline) -> None:
    section("3. Tickets con dos problemas (uno de 'siempre persona')")
    result = route(model, load_phrases("multi_problem"))
    leaked = result.loc[result["dest"] != HUMAN]
    print(f"Van a la IA (ignorando el problema grave): {len(leaked)} de {len(result)}")
    show_rows(leaked, ["pred", "confidence", "p_human", "dest"])
    print("A persona:")
    show_rows(result.loc[result["dest"] == HUMAN], ["pred", "confidence", "p_human"])


def show_long_messages(model: Pipeline) -> None:
    section("4. Tickets largos y realistas")
    lines = [line.split(" | ", 1) for line in load_phrases("long_messages")]
    expected, texts = zip(*lines, strict=True)
    result = route(model, list(texts)).assign(expected=expected)
    result["ok"] = result["pred"] == result["expected"]
    result["expected_route"] = [INTENT_ROUTE[e] for e in expected]
    words = result["text"].str.split().str.len()
    print(
        f"{len(result)} tickets · {words.median():.0f} palabras de mediana (Banking77: 10)"
    )
    print(
        f"Intención acertada: {result['ok'].sum()} de {len(result)} · confianza mediana {result['confidence'].median():.2f}"
    )
    leaked = result[(result["dest"] != HUMAN) & (result["expected_route"] == HUMAN)]
    print(f"Debían ir a persona y van a la IA: {len(leaked)}\n")
    show_rows(result, ["expected", "pred", "confidence", "dest"])


def main() -> None:
    train, test = load_data()
    model = build_model().fit(train["text"], train["category"])
    config = (
        ROUTER.min_confidence,
        ROUTER.max_p_human,
        ROUTER.canned_min_confidence,
    )
    print(
        f"Router de rules/intents.yaml (umbral, límite P(persona), predefinidas) = {config}"
    )
    show_out_of_scope(model)
    show_hidden_intents(train, test)
    show_multi_problem(model)
    show_long_messages(model)


if __name__ == "__main__":
    main()
