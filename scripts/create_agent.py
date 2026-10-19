"""Crea un agente de soporte: la única forma de tener un usuario con role=agent.

    uv run python scripts/create_agent.py agente@banco.com --name "Ana"
    mise run agent:create -- agente@banco.com --name "Ana"

La contraseña se pide por teclado (getpass): no queda en el historial de la
shell ni en la lista de procesos, como pasaría si fuera un argumento.
"""

import argparse
import asyncio
import getpass
import sys

from pydantic import ValidationError

from revision_rag.core.config import get_settings
from revision_rag.core.exceptions import EmailAlreadyRegisteredError
from revision_rag.db.session import build_engine, build_sessionmaker
from revision_rag.repositories.refresh_token import RefreshTokenRepository
from revision_rag.repositories.user import UserRepository
from revision_rag.schemas.user import AgentCreate
from revision_rag.services.auth import AuthService


async def create_agent(data: AgentCreate) -> None:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    try:
        async with build_sessionmaker(engine)() as session:
            # El mismo servicio que usa la API: hash, email normalizado y el
            # error de email repetido, sin duplicar nada.
            service = AuthService(
                session,
                UserRepository(session),
                RefreshTokenRepository(session),
                settings,
            )
            user = await service.create_agent(data)
            print(f"Agente creado: {user.email} ({user.id})")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Crea un agente de soporte.")
    parser.add_argument("email")
    parser.add_argument("--name", dest="display_name", help="nombre visible")
    args = parser.parse_args()

    password = getpass.getpass("Contraseña: ")
    if getpass.getpass("Repite la contraseña: ") != password:
        sys.exit("Las contraseñas no coinciden")

    try:
        data = AgentCreate(
            email=args.email, password=password, display_name=args.display_name
        )
    except ValidationError as exc:
        errors = "\n".join(
            f"  {'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()
        )
        sys.exit(f"Datos no válidos:\n{errors}")

    try:
        asyncio.run(create_agent(data))
    except EmailAlreadyRegisteredError as exc:
        sys.exit(exc.detail)


if __name__ == "__main__":
    main()
