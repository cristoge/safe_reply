from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from revision_rag.api.routes import auth, users
from revision_rag.core.config import get_settings
from revision_rag.core.exceptions import AppError
from revision_rag.db.session import DbSession, build_engine, build_sessionmaker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Arranque: se crea el engine una sola vez y se comprueba la conexión.
    # Si la base de datos no responde, la app no llega a arrancar (fail fast).
    engine = build_engine(get_settings().database_url)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    app.state.engine = engine
    app.state.sessionmaker = build_sessionmaker(engine)

    yield

    # Apagado: se cierran las conexiones del pool.
    await engine.dispose()


app = FastAPI(title="revision-rag", lifespan=lifespan)


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    # Traduce los errores de negocio a HTTP con el mismo formato que usa
    # FastAPI en sus errores ({"detail": ...}).
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")


@app.get("/health")
async def health(db: DbSession) -> JSONResponse:
    try:
        await db.execute(text("SELECT 1"))
    # Solo errores de conexión: un bug en el código no debe disfrazarse de "base de datos caída".
    except SQLAlchemyError, OSError:
        return JSONResponse(
            status_code=503, content={"status": "error", "database": "unreachable"}
        )
    return JSONResponse(content={"status": "ok", "database": "ok"})
