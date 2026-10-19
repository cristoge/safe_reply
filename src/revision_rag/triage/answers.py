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

# Hasta el S6, las intenciones sin respuesta revisada usan este texto. Genérico
# a propósito: no promete nada y deja abierta la salida a una persona.
PROVISIONAL_ANSWER = (
    "Thanks for your message. We're preparing a detailed answer for this topic.\n"
    'If you still need help, reply to this message or press "I still need help" '
    "and a member of our team will call you."
)


class CannedAnswers:
    """Las respuestas predefinidas revisadas, una por intención (policies/canned/).

    Se cargan una vez al arrancar: como mucho 77 textos, en memoria.
    """

    def __init__(self, answers: Mapping[str, str]) -> None:
        self._answers = dict(answers)

    @classmethod
    def from_dir(cls, directory: Path) -> CannedAnswers:
        # policies/canned/<intent>.md. Si la carpeta aún no existe (antes del
        # S6), no hay ninguna: todas usan la provisional.
        if not directory.is_dir():
            return cls({})
        return cls(
            {
                path.stem: path.read_text(encoding="utf-8").strip()
                for path in sorted(directory.glob("*.md"))
            }
        )

    def for_intent(self, intent: str) -> str:
        return self._answers.get(intent, PROVISIONAL_ANSWER)

    def __len__(self) -> int:
        return len(self._answers)
