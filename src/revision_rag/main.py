from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from revision_rag.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Arranque: se crea el engine una sola vez y se comprueba la conexión.
    # Si la base de datos no responde, la app no llega a arrancar (fail fast).
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    app.state.engine = engine

    yield

    # Apagado: se cierran las conexiones del pool.
    await engine.dispose()


app = FastAPI(title="revision-rag", lifespan=lifespan)


@app.get("/health")
async def health(request: Request) -> JSONResponse:
    engine: AsyncEngine = request.app.state.engine
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    # Solo errores de conexión: un bug en el código no debe disfrazarse de "base de datos caída".
    except SQLAlchemyError, OSError:
        return JSONResponse(
            status_code=503, content={"status": "error", "database": "unreachable"}
        )
    return JSONResponse(content={"status": "ok", "database": "ok"})
