"""Cómo se lanza el procesado de un ticket: el puerto TaskQueue y la versión de la v1.

v1: InlineTaskQueue lo ejecuta al momento, en la misma petición (sin LLM son
milisegundos). v2: un ArqTaskQueue lo dejará en Redis para un worker. Ni
TicketService ni process_ticket cambian: solo qué cola se inyecta.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol


class TaskQueue(Protocol):
    async def enqueue_ticket(self, ticket_id: uuid.UUID) -> None: ...


class InlineTaskQueue:
    def __init__(self, process_ticket: Callable[[uuid.UUID], Awaitable[None]]) -> None:
        # Recibe la función y no el TicketProcessor: la cola no necesita saber
        # nada del procesado (y así no hay import circular con los servicios).
        self._process_ticket = process_ticket

    async def enqueue_ticket(self, ticket_id: uuid.UUID) -> None:
        await self._process_ticket(ticket_id)
