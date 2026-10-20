"""Consultas de alerta: la cola, los atascos, el % a persona y los escalados.

    uv run python scripts/alerts.py        o        mise run alerts

Los umbrales están en rules/intents.yaml (alerts). Sale con código 1 si salta
alguna alerta: así se puede ejecutar desde cron o CI y avisar solo entonces.
"""

import asyncio
import sys
from datetime import UTC, datetime

from safe_reply.core.config import get_settings
from safe_reply.db.session import build_engine, build_sessionmaker
from safe_reply.repositories.ticket_stats import TicketStatsRepository
from safe_reply.services.alerts import AlertReport, AlertService
from safe_reply.triage.rules import load_rules


async def check() -> AlertReport:
    settings = get_settings()
    rules = load_rules(settings.rules_path)
    engine = build_engine(settings.database_url)
    try:
        async with build_sessionmaker(engine)() as session:
            service = AlertService(TicketStatsRepository(session), rules)
            return await service.check(datetime.now(UTC))
    finally:
        await engine.dispose()


def show(report: AlertReport, now: datetime) -> None:
    queue = report.queue
    share = "—" if report.human_share is None else f"{report.human_share:.0%}"
    print(f"Procesados en la ventana: {report.processed} · a persona: {share}")
    print(
        f"Cola: {queue.waiting} esperando ({queue.unassigned} sin asignar) ·"
        f" {queue.overdue} con el plazo vencido"
    )
    if queue.oldest_created_at is not None:
        hours = (now - queue.oldest_created_at).total_seconds() / 3600
        print(f"El más viejo sin atender: hace {hours:.1f} h")
    print(f"Sin procesar (atascados): {report.stuck}")
    if report.escalations:
        print("\nEscalados por intención (respondidos por la IA):")
        for e in sorted(
            report.escalations, key=lambda e: e.escalated / e.answered, reverse=True
        ):
            print(
                f"  {e.escalated / e.answered:>4.0%}  {e.escalated:>4} de"
                f" {e.answered:<5} {e.intent}"
            )

    print()
    for alert in report.alerts:
        print(f"⚠️  {alert}")
    if not report.alerts:
        print("Sin alertas")


def main() -> None:
    report = asyncio.run(check())
    show(report, datetime.now(UTC))
    sys.exit(1 if report.alerts else 0)


if __name__ == "__main__":
    main()
