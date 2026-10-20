"""core/logging.py: una línea JSON por evento."""

import json
import logging
import sys
import uuid

from safe_reply.core.logging import JsonFormatter


def record(**extra: object) -> logging.LogRecord:
    logger = logging.getLogger("safe_reply.test")
    return logger.makeRecord(
        logger.name, logging.INFO, __file__, 1, "ticket_triaged", (), None, extra=extra
    )


def test_one_json_line_with_the_event_and_its_fields() -> None:
    ticket_id = uuid.uuid7()

    line = JsonFormatter().format(record(ticket_id=ticket_id, priority=3))

    entry = json.loads(line)
    assert "\n" not in line
    assert entry["event"] == "ticket_triaged"
    assert entry["level"] == "INFO"
    assert entry["ticket_id"] == str(ticket_id)  # uuid sin convertir a mano
    assert entry["priority"] == 3
    # Solo lo de extra=: nada de los atributos internos del LogRecord.
    assert "pathname" not in entry and "args" not in entry


def test_exceptions_go_in_the_same_line() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        logger = logging.getLogger("safe_reply.test")
        rec = logger.makeRecord(
            logger.name, logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )

    entry = json.loads(JsonFormatter().format(rec))

    assert "ValueError: boom" in entry["exc_info"]
