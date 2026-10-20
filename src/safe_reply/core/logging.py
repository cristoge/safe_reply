"""Logs estructurados: una línea JSON por evento (S7).

El mensaje es el nombre del evento ("ticket_triaged") y los datos van en
extra={...}: así se pueden filtrar y contar sin parsear frases.

Nunca el texto del cliente, su teléfono o email, ni notas o motivos escritos a
mano: son datos personales y un log se copia, se envía y se guarda en sitios
que no controlamos. Los ids bastan para ir a la BD a mirar el resto.
"""

import json
import logging
import sys
from datetime import UTC, datetime

# Los atributos que trae cualquier LogRecord: el resto son los de extra=.
_RECORD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry |= {k: v for k, v in vars(record).items() if k not in _RECORD_ATTRS}
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        # default=str: uuid, datetime y enums sin convertirlos a mano.
        return json.dumps(entry, default=str, ensure_ascii=False)


def configure_logging(level: str) -> None:
    """Los logs de la app (logger "safe_reply") en JSON por stdout.

    Solo los nuestros: los de uvicorn siguen con su formato. Sin propagar al
    root, para no duplicarlos si alguien le pone un handler.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("safe_reply")
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
