"""Lo que recibe el cliente sin LLM: la plantilla de llamada y las respuestas predefinidas.

Textos en inglés (los clientes escriben en inglés). Lo que el sistema dice
compromete al banco: nada de plazos que no se puedan cumplir.
"""

from collections.abc import Mapping
from pathlib import Path

# Sin LLM en este camino: el texto del cliente nunca llega a un modelo que
# conteste. El aviso es por el vishing: las llamadas "del banco" son el fraude
# más común, y el cliente va a recibir una.
CALL_TEMPLATE = (
    "Thanks for contacting us. A member of our team will call you as soon as "
    "possible.\n"
    "We will never ask for your PIN, passwords or one-time codes over the phone."
)

FRAUD_TICKET_TYPE = "security_and_fraud"


def unreachable_message(failed_calls: int, ticket_type: str | None) -> str:
    """Lo que recibe el cliente tras las llamadas fallidas del YAML (S5).

    Que llame él al número de la app encaja con el aviso de vishing: así no
    puede ser una llamada falsa del banco. Sin "vaya a su sucursal": es un banco
    digital, no las tiene. Las rutas de la app son las de bank_facts.md (lo
    comprueba test_canned_answers.py).
    """
    message = (
        f"We tried to call you {failed_calls} times but couldn't reach you.\n"
        "Reply to this message with a good time to call you, or call us yourself "
        "on the number shown in the app (Help › Contact us)."
    )
    if ticket_type == FRAUD_TICKET_TYPE:
        # Mientras no hablamos con él, lo urgente es parar el daño.
        message += (
            "\nIf you think someone is using your card, freeze it now in the app "
            "(Cards › Freeze card)."
        )
    return message


class CannedAnswersError(Exception):
    """Faltan o sobran respuestas predefinidas: la app no debe arrancar (fail fast)."""


class CannedAnswers:
    """Las respuestas predefinidas revisadas, una por intención (policies/canned/).

    Se cargan una vez al arrancar: como mucho 77 textos, en memoria. Que haya
    exactamente una por intención de IA lo comprueba load_triage (main.py).
    """

    def __init__(self, answers: Mapping[str, str]) -> None:
        self._answers = dict(answers)

    @classmethod
    def from_dir(cls, directory: Path) -> CannedAnswers:
        # policies/canned/<intent>.md. Sin la carpeta no hay ninguna, y
        # load_triage no deja arrancar.
        return cls(
            {
                path.stem: path.read_text(encoding="utf-8").strip()
                for path in sorted(directory.glob("*.md"))
            }
        )

    @property
    def intents(self) -> frozenset[str]:
        return frozenset(self._answers)

    def for_intent(self, intent: str) -> str:
        # Sin texto de reserva: una intención sin respuesta no arranca la app.
        # Si aun así llegara aquí, el KeyError manda el ticket a persona
        # ("processing error"), nunca una respuesta inventada.
        return self._answers[intent]

    def __len__(self) -> int:
        return len(self._answers)
