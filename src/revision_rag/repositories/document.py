import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.models import Document


class DocumentRepository:
    """Acceso a la tabla documents. No hace commit(): eso lo decide el servicio.

    Todas las consultas filtran por project_id. Que el proyecto sea del usuario
    ya lo ha comprobado antes get_owned_project.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, document: Document) -> Document:
        self.session.add(document)
        await self.session.flush()
        return document

    async def get_for_project(
        self, document_id: uuid.UUID, project_id: uuid.UUID
    ) -> Document | None:
        stmt = select(Document).where(
            Document.id == document_id, Document.project_id == project_id
        )
        return await self.session.scalar(stmt)

    async def list_for_project(
        self, project_id: uuid.UUID, *, limit: int, offset: int
    ) -> Sequence[Document]:
        stmt = (
            select(Document)
            .where(Document.project_id == project_id)
            .order_by(Document.created_at.desc(), Document.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.scalars(stmt)).all()

    async def list_storage_keys(self, project_id: uuid.UUID) -> Sequence[str]:
        # Solo la columna que hace falta, sin cargar los documentos enteros.
        stmt = select(Document.storage_key).where(Document.project_id == project_id)
        return (await self.session.scalars(stmt)).all()

    async def delete(self, document: Document) -> None:
        # Los chunks y materiales los borra Postgres (ON DELETE CASCADE).
        await self.session.delete(document)
        await self.session.flush()
