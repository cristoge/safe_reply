"""RateLimiter contra el Redis de tests (base de datos 1)."""

import pytest
from redis.asyncio import Redis

from revision_rag.core.exceptions import RateLimitExceededError
from revision_rag.services.rate_limiter import RateLimiter


async def test_rate_limiter_allows_up_to_the_limit(redis: Redis) -> None:
    limiter = RateLimiter(redis, limit=3, window_seconds=60)
    for _ in range(3):
        await limiter.hit("key")

    with pytest.raises(RateLimitExceededError) as exc_info:
        await limiter.hit("key")
    assert 0 < exc_info.value.retry_after <= 60


async def test_rejected_hits_do_not_extend_the_window(redis: Redis) -> None:
    limiter = RateLimiter(redis, limit=1, window_seconds=60)
    await limiter.hit("key")
    # Simula que ya han pasado 50 s de la ventana.
    await redis.expire("rate_limit:key", 10)

    for _ in range(3):
        with pytest.raises(RateLimitExceededError) as exc_info:
            await limiter.hit("key")
        # Insistir no reinicia la ventana: sigue caducando a los 10 s, no a los 60.
        assert exc_info.value.retry_after <= 10


async def test_window_restarts_when_the_key_expires(redis: Redis) -> None:
    limiter = RateLimiter(redis, limit=1, window_seconds=60)
    await limiter.hit("key")
    await redis.delete("rate_limit:key")  # lo que hace Redis al caducar

    await limiter.hit("key")
