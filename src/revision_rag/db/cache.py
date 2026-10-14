from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis


def build_redis(redis_url: str) -> Redis:
    # Cliente async (redis.asyncio): no bloquea el event loop. Lleva dentro su
    # propio pool de conexiones, así que se crea uno solo para toda la app.
    # decode_responses=True: devuelve str en vez de bytes (la caché guarda JSON).
    return Redis.from_url(redis_url, decode_responses=True)


def get_redis(request: Request) -> Redis:
    """El cliente compartido. No hay uno por petición: no tiene transacción que aislar."""
    return request.app.state.redis


RedisClient = Annotated[Redis, Depends(get_redis)]
