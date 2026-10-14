from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, UploadFile, status

from revision_rag.api.deps import (
    DocumentServiceDep,
    OwnedDocument,
    OwnedProject,
    Pagination,
    SettingsDep,
)
from revision_rag.core.exceptions import (
    DocumentNotFoundError,
    FileTooLargeError,
    InvalidFileError,
    NotAuthenticatedError,
    ProjectNotFoundError,
    UnsupportedFileTypeError,
)
from revision_rag.models import Document
from revision_rag.schemas.document import DocumentRead

# Cuelga de un proyecto: todas las rutas pasan por get_owned_project.
router = APIRouter(
    prefix="/projects/{project_id}/documents",
    tags=["documents"],
    responses={
        NotAuthenticatedError.status_code: {
            "description": NotAuthenticatedError.detail
        },
        ProjectNotFoundError.status_code: {
            "description": f"{ProjectNotFoundError.detail} / {DocumentNotFoundError.detail}"
        },
    },
)

UPLOAD_ERRORS: dict[int | str, dict[str, Any]] = {
    error.status_code: {"description": error.detail}
    for error in (FileTooLargeError, UnsupportedFileTypeError, InvalidFileError)
}


@router.post(
    "",
    # 202 y no 201: el documento queda en "pending" y se procesará después. La
    # API es asíncrona desde el principio, así que no cambiará al añadir la ingesta.
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DocumentRead,
    responses=UPLOAD_ERRORS,
)
async def upload_document(
    project: OwnedProject,
    file: UploadFile,
    service: DocumentServiceDep,
    settings: SettingsDep,
) -> Document:
    # Se lee como mucho un byte más que el límite: basta para saber si lo
    # supera sin cargar en memoria un archivo enorme.
    content = await file.read(settings.max_upload_bytes + 1)
    return await service.upload(project, file.filename, content)


@router.get("", response_model=list[DocumentRead])
async def list_documents(
    project: OwnedProject, service: DocumentServiceDep, pagination: Pagination
) -> Sequence[Document]:
    return await service.list_documents(
        project, limit=pagination.limit, offset=pagination.offset
    )


@router.get("/{document_id}", response_model=DocumentRead)
async def read_document(document: OwnedDocument) -> Document:
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document: OwnedDocument, service: DocumentServiceDep) -> None:
    await service.delete(document)
