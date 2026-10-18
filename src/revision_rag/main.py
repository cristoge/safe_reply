from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from revision_rag.api.routes import auth, users
from revision_rag.core.config import Settings, get_settings
from revision_rag.core.exceptions import AppError
from revision_rag.db.cache import RedisClient, build_redis
from revision_rag.db.session import DbSession, build_engine, build_sessionmaker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Arranque: se crean el engine y el cliente de Redis una sola vez y se
    # comprueban las conexiones. Si alguno no responde, la app no llega a
    # arrancar (fail fast).
    settings = get_settings()
    engine = build_engine(settings.database_url)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    redis = build_redis(settings.redis_url)
    await redis.ping()
    app.state.engine = engine
    app.state.sessionmaker = build_sessionmaker(engine)
    app.state.redis = redis

    yield

    # Apagado: se cierran las conexiones de los dos pools.
    await redis.aclose()
    await engine.dispose()


app = FastAPI(title="revision-rag", lifespan=lifespan)


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    # Traduce los errores de negocio a HTTP con el mismo formato que usa
    # FastAPI en sus errores ({"detail": ...}).
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers
    )


def include_routers(app: FastAPI, settings: Settings) -> None:
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")
    # Rutas solo para desarrollo: en producción no existen (404 y fuera de /docs).
    if settings.environment == "development":
        app.include_router(users.dev_router, prefix="/api/v1")


include_routers(app, get_settings())


@app.get("/health")
async def health(db: DbSession, redis: RedisClient) -> JSONResponse:
    checks = {"database": "ok", "redis": "ok"}
    # Solo errores de conexión: un bug en el código no debe disfrazarse de "servicio caído".
    try:
        await db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        checks["database"] = "unreachable"
    try:
        await redis.ping()
    except RedisError:
        checks["redis"] = "unreachable"

    if "unreachable" in checks.values():
        return JSONResponse(status_code=503, content={"status": "error", **checks})
    return JSONResponse(content={"status": "ok", **checks})
