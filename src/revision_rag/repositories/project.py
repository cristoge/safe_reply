import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.models import Project


class ProjectRepository:
    """Acceso a la tabla projects. No hace commit(): eso lo decide el servicio.

    Todas las consultas filtran por user_id: no hay forma de pedir el proyecto
    de otro usuario.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, project: Project) -> Project:
        self.session.add(project)
        await self.session.flush()
        return project

    async def get_for_user(
        self, project_id: uuid.UUID, user_id: uuid.UUID
    ) -> Project | None:
        # Si el proyecto existe pero es de otro, da None igual que si no
        # existiera: desde fuera no se puede distinguir.
        stmt = select(Project).where(
            Project.id == project_id, Project.user_id == user_id
        )
        return await self.session.scalar(stmt)

    async def list_for_user(
        self, user_id: uuid.UUID, *, limit: int, offset: int
    ) -> Sequence[Project]:
        # Los más nuevos primero. El id desempata: es UUIDv7, ordenado por
        # tiempo, así que el orden es siempre el mismo entre páginas.
        stmt = (
            select(Project)
            .where(Project.user_id == user_id)
            .order_by(Project.created_at.desc(), Project.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.scalars(stmt)).all()

    async def delete(self, project: Project) -> None:
        # Los documentos, chunks, conversaciones... los borra Postgres con el
        # ON DELETE CASCADE (passive_deletes=True: SQLAlchemy no los carga).
        await self.session.delete(project)
        await self.session.flush()
