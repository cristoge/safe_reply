from dataclasses import dataclass, field
from datetime import datetime, timedelta

from revision_rag.models.enums import Route
from revision_rag.repositories.ticket_stats import (
    IntentEscalations,
    QueueSummary,
    TicketStatsRepository,
)
from revision_rag.triage.rules import Rules


@dataclass(frozen=True)
class AlertReport:
    """Las cifras y las alertas que saltan con los umbrales del YAML (alerts)."""

    processed: int
    human_share: float | None  # None: sin tickets en la ventana
    queue: QueueSummary
    stuck: int
    escalations: list[IntentEscalations]
    alerts: list[str] = field(default_factory=list)


class AlertService:
    """Lo que hay que mirar sin abrir la BD: la cola, los atascos y qué no funciona.

    Solo avisa: decidir qué cambiar (un umbral, una respuesta) es de una persona.
    Ajustarlo solo con estos datos sería peligroso (apartado 6 › Escalar: un
    cliente pulsando muchas veces subiría el umbral para todos).
    """

    def __init__(self, stats: TicketStatsRepository, rules: Rules) -> None:
        self.stats = stats
        self.config = rules.alerts

    async def check(self, now: datetime) -> AlertReport:
        config = self.config
        routes = await self.stats.route_counts(
            since=now - timedelta(hours=config.window_hours)
        )
        processed = sum(routes.values())
        human_share = routes.get(Route.HUMAN, 0) / processed if processed else None
        queue = await self.stats.queue_summary(now=now)
        stuck = await self.stats.count_stuck(
            before=now - timedelta(minutes=config.stuck_minutes)
        )
        escalations = await self.stats.escalations_by_intent(
            since=now - timedelta(days=config.escalation_window_days)
        )

        alerts = []
        if (
            human_share is not None
            and processed >= config.min_tickets
            and human_share > config.max_human_share
        ):
            alerts.append(
                f"{human_share:.0%} de tickets a persona en {config.window_hours} h"
                f" (máximo {config.max_human_share:.0%})"
            )
        if queue.overdue:
            alerts.append(f"{queue.overdue} tickets en la cola con el plazo vencido")
        if stuck:
            alerts.append(
                f"{stuck} tickets sin procesar desde hace más de"
                f" {config.stuck_minutes} min"
            )
        for e in escalations:
            rate = e.escalated / e.answered
            if e.answered >= config.min_tickets and rate > config.max_escalation_rate:
                alerts.append(
                    f"{e.intent}: {rate:.0%} de escalados ({e.escalated} de"
                    f" {e.answered}, máximo {config.max_escalation_rate:.0%})"
                )
        return AlertReport(processed, human_share, queue, stuck, escalations, alerts)
