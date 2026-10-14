import uuid
from collections.abc import Sequence
from pathlib import PurePath

from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.clients.storage import Storage, delete_files
from revision_rag.core.config import Settings
from revision_rag.core.exceptions import (
    DocumentNotFoundError,
    FileTooLargeError,
    InvalidFileError,
    UnsupportedFileTypeError,
)
from revision_rag.models import Document, Project
from revision_rag.models.enums import SourceType
from revision_rag.repositories.document import DocumentRepository

SUFFIXES = {".pdf": SourceType.PDF, ".md": SourceType.MARKDOWN}


def detect_source_type(filename: str, content: bytes) -> SourceType:
    """El tipo según la extensión, comprobando que el contenido cuadra con ella.

    No se mira el Content-Type de la petición: lo decide el cliente y puede
    decir cualquier cosa.
    """
    source_type = SUFFIXES.get(PurePath(filename).suffix.lower())
    if source_type is None:
        raise UnsupportedFileTypeError

    if source_type is SourceType.PDF:
        # "Magic bytes": todo PDF empieza por %PDF-. El estándar permite algo
        # de basura delante, por eso se busca en el primer KB y no solo al
        # principio. Un .txt renombrado a .pdf no pasa.
        if b"%PDF-" not in content[:1024]:
            raise InvalidFileError("El archivo no es un PDF válido")
    else:
        # Un .md no tiene magic bytes: lo único comprobable es que sea texto.
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidFileError("El archivo .md no es texto UTF-8") from exc
    return source_type


def clean_filename(filename: str) -> str:
    # Algunos navegadores mandan la ruta entera ("C:\fakepath\notas.pdf"): solo
    # interesa el nombre. Se guarda solo para mostrarlo, nunca como ruta.
    return filename.replace("\\", "/").rsplit("/", 1)[-1][:255]


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        documents: DocumentRepository,
        storage: Storage,
        settings: Settings,
    ) -> None:
        self.session = session
        self.documents = documents
        self.storage = storage
        self.settings = settings

    async def upload(
        self, project: Project, filename: str | None, content: bytes
    ) -> Document:
        if len(content) > self.settings.max_upload_bytes:
            max_mb = self.settings.max_upload_bytes // (1024 * 1024)
            raise FileTooLargeError(f"El archivo supera el máximo de {max_mb} MB")
        if not content:
            raise InvalidFileError("El archivo está vacío")
        filename = clean_filename(filename or "")
        if not filename:
            raise InvalidFileError("Falta el nombre del archivo")
        source_type = detect_source_type(filename, content)

        # El id se genera aquí (y no al insertar) para usarlo también en la
        # ruta del archivo: nunca se usa el nombre que manda el cliente.
        document_id = uuid.uuid7()
        storage_key = (
            f"projects/{project.id}/{document_id}{PurePath(filename).suffix.lower()}"
        )
        document = Document(
            id=document_id,
            project_id=project.id,
            source_type=source_type,
            original_filename=filename,
            storage_key=storage_key,
            size_bytes=len(content),
            embedding_model=self.settings.embedding_model,
        )

        # Primero el archivo, después la fila. Si falla la BD, se borra el
        # archivo: nunca queda una fila apuntando a un archivo que no existe.
        await self.storage.save(storage_key, content)
        try:
            await self.documents.add(document)
            await self.session.commit()
        except Exception:
            await self.storage.delete(storage_key)
            raise
        return document

    async def list_documents(
        self, project: Project, *, limit: int, offset: int
    ) -> Sequence[Document]:
        return await self.documents.list_for_project(
            project.id, limit=limit, offset=offset
        )

    async def get(self, project: Project, document_id: uuid.UUID) -> Document:
        # El proyecto ya está comprobado (es del usuario): basta con que el
        # documento sea de ese proyecto.
        document = await self.documents.get_for_project(document_id, project.id)
        if document is None:
            raise DocumentNotFoundError
        return document

    async def delete(self, document: Document) -> None:
        # Al revés que al subir: primero la fila (con su commit) y después el
        # archivo. Si falla el borrado del archivo, queda huérfano, pero la app
        # ya no lo ve.
        storage_key = document.storage_key
        await self.documents.delete(document)
        await self.session.commit()
        await delete_files(self.storage, [storage_key])
