"""Evaluación de la v1 tal como arranca (apartado 11 del diseño).

compare_models.py y robustness.py exploran: reentrenan y barren umbrales. Este
script mide lo que de verdad se ejecuta: lo carga con load_triage, como la app
(rules/intents.yaml + models/ + policies/canned/), y pasa cada ticket por
triage_ticket, la misma función que el procesado.

Mide, con el umbral de las predefinidas del YAML y con las alternativas de
CANNED_CANDIDATES (para elegirlo con datos):
- Respuestas incorrectas enviadas (la métrica clave): la IA respondió un ticket
  cuya intención real es de persona.
- % de llamadas (el coste) y % de respuestas automáticas.
- Respuestas con la intención mal: del mismo tipo (molesta) o de otro (grave).
- Prioridad de los tickets que llegan a la cola: ¿la misma que con la intención real?
- Fuera de ámbito, dos problemas y tickets largos (ml/phrases/).

Escribe ml/results/evaluation.json (en Git): sin fecha ni commit, solo lo que
determina los números (versión del modelo y hashes de datos, reglas y
respuestas). Mismas entradas → mismo archivo; si cambia, el diff dice qué cambió.

Uso (desde la raíz del repo, con models/ y .env): uv run python ml/evaluate.py
"""

import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from common import DATA_DIR, ML_DIR, load_data, load_phrases
from sklearn.metrics import accuracy_score, f1_score

from safe_reply.clients.classifier import IntentClassifier
from safe_reply.core.config import get_settings
from safe_reply.main import load_triage
from safe_reply.models.enums import Route
from safe_reply.triage.answers import CannedAnswers
from safe_reply.triage.pipeline import (
    CANNED_RAG_DISABLED,
    TriageResult,
    triage_ticket,
)
from safe_reply.triage.router import CANNED_FAQ
from safe_reply.triage.rules import Rules
from safe_reply.triage.urgency import final_priority

RESULTS_PATH = ML_DIR / "results" / "evaluation.json"
# Las dos opciones del S6 (apartado 3.5 del diseño): 0.8 responde más, 0.9 se
# equivoca menos. La del YAML se añade siempre.
CANNED_CANDIDATES = (0.8, 0.9)
# Cada ticket como si fuera el único del cliente: la señal "escribe por tercera
# vez" no se puede medir con Banking77.
RECENT_TICKETS = 1
# Ejemplos que se guardan por lista: suficientes para ver el patrón.
MAX_EXAMPLES = 20


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def answers_sha256(answers_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(answers_dir.glob("*.md")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def with_canned_min_confidence(rules: Rules, value: float) -> Rules:
    router = rules.router.model_copy(update={"canned_min_confidence": value})
    return rules.model_copy(update={"router": router})


def share(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def labelled_metrics(
    texts: Sequence[str],
    expected: Sequence[str],
    results: Sequence[TriageResult],
    rules: Rules,
) -> dict:
    """Las métricas de un conjunto con la intención real de cada ticket."""
    n = len(results)
    to_ai = [r.route == Route.AI for r in results]
    truly_human = [rules.intents[y].route == Route.HUMAN for y in expected]

    wrong_sent = [
        (y, r.intent, t)
        for t, y, r, ai, human in zip(
            texts, expected, results, to_ai, truly_human, strict=True
        )
        if ai and human
    ]
    wrong_intent = [
        (y, r)
        for y, r, ai in zip(expected, results, to_ai, strict=True)
        if ai and r.intent != y
    ]
    other_type = [
        (y, r) for y, r in wrong_intent if rules.intents[y].ticket_type != r.ticket_type
    ]
    avoidable_calls = sum(
        not ai and not human for ai, human in zip(to_ai, truly_human, strict=True)
    )

    # La prioridad ordena la cola: solo cuenta en los tickets que llegan a ella.
    queued = [
        (
            r.priority.level,
            final_priority(rules.intents[y].priority, t, RECENT_TICKETS, rules).level,
        )
        for t, y, r in zip(texts, expected, results, strict=True)
        if r.route == Route.HUMAN
    ]
    human_reasons = Counter(
        r.route_reason.split(":")[0] for r in results if r.route == Route.HUMAN
    )
    return {
        "tickets": n,
        "wrong_answers_sent": len(wrong_sent),
        "calls": share(n - sum(to_ai), n),
        "ai_answers": share(sum(to_ai), n),
        "canned_faq": share(sum(r.route_reason == CANNED_FAQ for r in results), n),
        "canned_rag_off": share(
            sum(r.route_reason == CANNED_RAG_DISABLED for r in results), n
        ),
        "ai_wrong_intent": len(wrong_intent),
        "ai_wrong_intent_other_type": len(other_type),
        # Tickets que la IA podía responder y acaban en una llamada.
        "avoidable_calls": share(avoidable_calls, n),
        "queue_priority_ok": share(sum(p == e for p, e in queued), len(queued)),
        # Más baja que la real: el ticket espera más de lo que debería.
        "queue_priority_too_low": sum(p < e for p, e in queued),
        # Más alta: cuesta orden en la cola, no seguridad (adelanta a otros).
        "queue_priority_too_high": sum(p > e for p, e in queued),
        "human_reasons": dict(human_reasons.most_common()),
        "wrong_answers_sent_examples": [
            {"expected": y, "predicted": p, "text": t}
            for y, p, t in wrong_sent[:MAX_EXAMPLES]
        ],
        "wrong_intent_other_type_pairs": dict(
            Counter(f"{r.intent} ← {y}" for y, r in other_type).most_common(
                MAX_EXAMPLES
            )
        ),
    }


def unlabelled_metrics(texts: Sequence[str], results: Sequence[TriageResult]) -> dict:
    """Frases que nunca deberían responderse sin una persona."""
    answered = [
        {"predicted": r.intent, "confidence": round(r.confidence, 2), "text": t}
        for t, r in zip(texts, results, strict=True)
        if r.route == Route.AI
    ]
    return {
        "tickets": len(results),
        "answered_by_ai": len(answered),
        "answered_by_ai_examples": answered[:MAX_EXAMPLES],
    }


def evaluate(
    probabilities: list[dict[str, float]],
    texts: Sequence[str],
    rules: Rules,
    answers: CannedAnswers,
) -> list[TriageResult]:
    return [
        triage_ticket(t, p, RECENT_TICKETS, rules, answers)
        for t, p in zip(texts, probabilities, strict=True)
    ]


def predict_all(
    classifier: IntentClassifier, texts: Sequence[str]
) -> list[dict[str, float]]:
    # Uno a uno, como en la app (~0.5 ms cada uno): lo que se mide es lo que se ejecuta.
    return [classifier.predict(t) for t in texts]


def main() -> None:
    settings = get_settings()
    rules, classifier, answers = load_triage(settings)
    _, test = load_data()
    test_texts, test_y = list(test["text"]), list(test["category"])

    long_lines = [line.split(" | ", 1) for line in load_phrases("long_messages")]
    sets = {
        "out_of_scope": load_phrases("out_of_scope"),
        "multi_problem": load_phrases("multi_problem"),
        "long_messages": [text for _, text in long_lines],
    }
    long_y = [intent for intent, _ in long_lines]

    print("Clasificando...")
    test_proba = predict_all(classifier, test_texts)
    sets_proba = {name: predict_all(classifier, texts) for name, texts in sets.items()}

    predicted = [max(p, key=p.__getitem__) for p in test_proba]
    report: dict = {
        "inputs": {
            "classifier_version": classifier.version,
            "test_sha256": sha256(DATA_DIR / "test.csv"),
            "rules_sha256": sha256(settings.rules_path),
            "canned_answers_sha256": answers_sha256(settings.canned_dir),
            "recent_tickets": RECENT_TICKETS,
        },
        "router": rules.router.model_dump(),
        "classifier": {
            "accuracy": round(accuracy_score(test_y, predicted), 4),
            "f1_macro": round(f1_score(test_y, predicted, average="macro"), 4),
        },
        "by_canned_min_confidence": {},
    }

    current = rules.router.canned_min_confidence
    # None (sin predefinidas) solo lo usan los barridos de compare_models.py.
    assert current is not None, "rules/intents.yaml siempre fija canned_min_confidence"
    candidates = sorted({*CANNED_CANDIDATES, current})
    for value in candidates:
        variant = with_canned_min_confidence(rules, value)
        long_results = evaluate(
            sets_proba["long_messages"], sets["long_messages"], variant, answers
        )
        report["by_canned_min_confidence"][str(value)] = {
            "banking77_test": labelled_metrics(
                test_texts,
                test_y,
                evaluate(test_proba, test_texts, variant, answers),
                variant,
            ),
            "out_of_scope": unlabelled_metrics(
                sets["out_of_scope"],
                evaluate(
                    sets_proba["out_of_scope"], sets["out_of_scope"], variant, answers
                ),
            ),
            # Todas tienen un problema de persona: ninguna debería responderla la IA.
            "multi_problem": unlabelled_metrics(
                sets["multi_problem"],
                evaluate(
                    sets_proba["multi_problem"], sets["multi_problem"], variant, answers
                ),
            ),
            "long_messages": labelled_metrics(
                sets["long_messages"], long_y, long_results, variant
            )
            | {
                "intent_ok": sum(
                    r.intent == y for r, y in zip(long_results, long_y, strict=True)
                )
            },
        }

    RESULTS_PATH.parent.mkdir(exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    show(report, current)
    print(f"\nGuardado en {RESULTS_PATH.relative_to(ML_DIR.parent)}")


def show(report: dict, current: float) -> None:
    inputs, clf = report["inputs"], report["classifier"]
    print(f"\nModelo {inputs['classifier_version']} · router {report['router']}")
    print(f"Clasificador: accuracy {clf['accuracy']} · F1 macro {clf['f1_macro']}\n")

    rows = {}
    for value, result in report["by_canned_min_confidence"].items():
        test, long = result["banking77_test"], result["long_messages"]
        label = f"predefinidas ≥ {value}" + (
            " (YAML)" if float(value) == current else ""
        )
        rows[label] = {
            "respuestas incorrectas enviadas": test["wrong_answers_sent"],
            "llamadas": f"{test['calls']:.1%}",
            "responde la IA": f"{test['ai_answers']:.1%}",
            "  predefinida FAQ": f"{test['canned_faq']:.1%}",
            "  predefinida (RAG apagado)": f"{test['canned_rag_off']:.1%}",
            "IA con la intención mal": test["ai_wrong_intent"],
            "  de otro tipo": test["ai_wrong_intent_other_type"],
            "llamadas evitables": f"{test['avoidable_calls']:.1%}",
            "prioridad en la cola ok": f"{test['queue_priority_ok']:.1%}",
            "  más baja que la real": test["queue_priority_too_low"],
            "  más alta que la real": test["queue_priority_too_high"],
            "fuera de ámbito → IA": f"{result['out_of_scope']['answered_by_ai']} de {result['out_of_scope']['tickets']}",
            "dos problemas → IA": f"{result['multi_problem']['answered_by_ai']} de {result['multi_problem']['tickets']}",
            "largos: intención ok": f"{long['intent_ok']} de {long['tickets']}",
            "largos: incorrectas enviadas": long["wrong_answers_sent"],
        }
    print(f"Test de Banking77 ({inputs['test_sha256'][:8]}) y frases de ml/phrases/:")
    print(pd.DataFrame(rows).to_string())

    current_result = report["by_canned_min_confidence"][str(current)]
    test = current_result["banking77_test"]
    print(f"\nCon el YAML ({current}) — motivos de las llamadas:")
    for reason, count in test["human_reasons"].items():
        print(f"  {count:>5}  {reason}")
    print("\nRespuestas incorrectas enviadas (real → predicha):")
    for e in test["wrong_answers_sent_examples"]:
        print(f"  [{e['expected']} → {e['predicted']}] {e['text']}")
    if not test["wrong_answers_sent_examples"]:
        print("  ninguna")
    print("\nIA con una intención de otro tipo (predicha ← real):")
    for pair, count in test["wrong_intent_other_type_pairs"].items():
        print(f"  {count:>3}  {pair}")
    if not test["wrong_intent_other_type_pairs"]:
        print("  ninguna")


if __name__ == "__main__":
    main()
