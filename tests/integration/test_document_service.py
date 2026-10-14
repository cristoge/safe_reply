import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.clients.storage import LocalStorage
from revision_rag.core.config import get_settings
from revision_rag.models import Document, Project
from revision_rag.repositories.document import DocumentRepository
from revision_rag.services.document import DocumentService


class FailingDocumentRepository(DocumentRepository):
    async def add(self, document: Document) -> Document:
        raise RuntimeError("la base de datos se ha caído")


async def test_upload_removes_the_file_if_the_database_fails(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    service = DocumentService(
        db_session, FailingDocumentRepository(db_session), storage, get_settings()
    )
    project = Project(id=uuid.uuid7(), user_id=uuid.uuid7(), name="AWS")

    with pytest.raises(RuntimeError):
        await service.upload(project, "VPC.md", b"# VPC")

    # El archivo llegó a guardarse, pero se borra: no queda nada huérfano.
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []
