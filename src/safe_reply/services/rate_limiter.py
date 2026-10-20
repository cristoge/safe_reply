from redis.asyncio import Redis

from safe_reply.core.exceptions import RateLimitExceededError


class RateLimiter:
    """Ventana fija en Redis: como mucho `limit` acciones cada `window_seconds`.

    Ventana fija y no deslizante: es un INCR por petición y, en el peor caso
    (justo en el cambio de ventana), deja pasar 2 × limit. Para frenar spam de
    tickets basta; un límite exacto pediría una ventana deslizante (ZSET).
    """

    def __init__(self, redis: Redis, *, limit: int, window_seconds: int) -> None:
        self.redis = redis
        self.limit = limit
        self.window_seconds = window_seconds

    async def hit(self, key: str) -> None:
        """Cuenta una acción. Lanza RateLimitExceededError si se pasa del límite."""
        redis_key = f"rate_limit:{key}"
        # MULTI/EXEC: las tres órdenes van juntas. EXPIRE ... NX solo pone la
        # caducidad la primera vez: así la ventana empieza con la primera acción
        # y no se alarga con cada intento (con un EXPIRE normal, alguien que
        # insiste no saldría nunca del bloqueo).
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(redis_key)
            pipe.expire(redis_key, self.window_seconds, nx=True)
            pipe.ttl(redis_key)
            count, _, ttl = await pipe.execute()

        if count > self.limit:
            # Las acciones rechazadas también cuentan, pero no alargan la
            # ventana: al caducar la clave, el contador vuelve a cero.
            raise RateLimitExceededError(retry_after=max(ttl, 1))
