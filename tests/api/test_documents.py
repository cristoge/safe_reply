import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from revision_rag.clients.storage import LocalStorage
from revision_rag.core.config import get_settings
from revision_rag.main import app

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF"
MARKDOWN = "# VPC\n\nUna red privada en AWS. Sección con tildes y ñ.".encode()


def stored_files(storage: LocalStorage) -> list[Path]:
    return [path for path in storage.base_dir.rglob("*") if path.is_file()]


async def create_project(client: AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(
        "/api/v1/projects", json={"name": "AWS"}, headers=headers
    )
    assert response.status_code == 201
    return response.json()["id"]


async def upload(
    client: AsyncClient,
    headers: dict[str, str],
    project_id: str,
    filename: str = "VPC.md",
    content: bytes = MARKDOWN,
) -> Any:
    return await client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": (filename, content)},
        headers=headers,
    )


@pytest.fixture
async def project_id(client: AsyncClient, auth_headers: dict[str, str]) -> str:
    return await create_project(client, auth_headers)


@pytest.mark.parametrize(
    ("filename", "content", "source_type"),
    [("VPC.md", MARKDOWN, "markdown"), ("Apuntes IAM.pdf", PDF, "pdf")],
)
async def test_upload_returns_202_pending_and_saves_the_file(
    client: AsyncClient,
    auth_headers: dict[str, str],
    project_id: str,
    storage: LocalStorage,
    filename: str,
    content: bytes,
    source_type: str,
) -> None:
    response = await upload(client, auth_headers, project_id, filename, content)

    assert response.status_code == 202
    body = response.json()
    assert body["original_filename"] == filename
    assert body["source_type"] == source_type
    assert body["size_bytes"] == len(content)
    assert body["status"] == "pending"
    assert "storage_key" not in body
    # El archivo se guarda con el id del documento, no con el nombre original.
    [saved] = stored_files(storage)
    assert saved.read_bytes() == content
    assert saved.name == f"{body['id']}{Path(filename).suffix}"


@pytest.mark.parametrize(
    ("filename", "content", "status_code", "detail"),
    [
        ("notas.txt", b"hola", 415, "Tipo de archivo no soportado: solo .pdf y .md"),
        ("falso.pdf", b"no soy un pdf", 422, "El archivo no es un PDF válido"),
        (
            "notas.md",
            "ñandú".encode("latin-1"),
            422,
            "El archivo .md no es texto UTF-8",
        ),
        ("vacio.md", b"", 422, "El archivo está vacío"),
    ],
    ids=["extensión no soportada", "pdf falso", "md no utf-8", "vacío"],
)
async def test_upload_invalid_file_is_rejected_and_nothing_is_saved(
    client: AsyncClient,
    auth_headers: dict[str, str],
    project_id: str,
    storage: LocalStorage,
    filename: str,
    content: bytes,
    status_code: int,
    detail: str,
) -> None:
    response = await upload(client, auth_headers, project_id, filename, content)

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    assert stored_files(storage) == []


@pytest.fixture
def small_upload_limit() -> Iterator[None]:
    settings = get_settings().model_copy(update={"max_upload_bytes": 10})
    app.dependency_overrides[get_settings] = lambda: settings
    yield
    app.dependency_overrides.pop(get_settings, None)


async def test_upload_too_large_returns_413(
    client: AsyncClient,
    auth_headers: dict[str, str],
    project_id: str,
    storage: LocalStorage,
    small_upload_limit: None,
) -> None:
    response = await upload(client, auth_headers, project_id, content=b"# " + b"x" * 20)

    assert response.status_code == 413
    assert stored_files(storage) == []


async def test_list_documents_returns_only_this_project_newest_first(
    client: AsyncClient, auth_headers: dict[str, str], project_id: str
) -> None:
    other_project_id = await create_project(client, auth_headers)
    await upload(client, auth_headers, project_id, "primero.md")
    await upload(client, auth_headers, other_project_id, "de-otro-proyecto.md")
    await upload(client, auth_headers, project_id, "segundo.md")

    response = await client.get(
        f"/api/v1/projects/{project_id}/documents", headers=auth_headers
    )

    assert response.status_code == 200
    assert [doc["original_filename"] for doc in response.json()] == [
        "segundo.md",
        "primero.md",
    ]


async def test_read_document(
    client: AsyncClient, auth_headers: dict[str, str], project_id: str
) -> None:
    document = (await upload(client, auth_headers, project_id)).json()

    response = await client.get(
        f"/api/v1/projects/{project_id}/documents/{document['id']}",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == document


async def test_document_from_another_project_returns_404(
    client: AsyncClient, auth_headers: dict[str, str], project_id: str
) -> None:
    # Mismo usuario, pero el documento no cuelga de ese proyecto.
    other_project_id = await create_project(client, auth_headers)
    document = (await upload(client, auth_headers, project_id)).json()

    response = await client.get(
        f"/api/v1/projects/{other_project_id}/documents/{document['id']}",
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Documento no encontrado"}


@pytest.mark.parametrize("method", ["GET", "DELETE"])
async def test_document_of_another_user_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    other_auth_headers: dict[str, str],
    storage: LocalStorage,
    method: str,
) -> None:
    other_project_id = await create_project(client, other_auth_headers)
    document = (await upload(client, other_auth_headers, other_project_id)).json()

    response = await client.request(
        method,
        f"/api/v1/projects/{other_project_id}/documents/{document['id']}",
        headers=auth_headers,
    )

    # Falla en el proyecto (no es tuyo), antes de llegar al documento.
    assert response.status_code == 404
    assert response.json() == {"detail": "Proyecto no encontrado"}
    assert len(stored_files(storage)) == 1


async def test_upload_to_a_project_of_another_user_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    other_auth_headers: dict[str, str],
    storage: LocalStorage,
) -> None:
    other_project_id = await create_project(client, other_auth_headers)

    response = await upload(client, auth_headers, other_project_id)

    assert response.status_code == 404
    assert stored_files(storage) == []


async def test_delete_document_removes_the_row_and_the_file(
    client: AsyncClient,
    auth_headers: dict[str, str],
    project_id: str,
    storage: LocalStorage,
) -> None:
    document = (await upload(client, auth_headers, project_id)).json()
    url = f"/api/v1/projects/{project_id}/documents/{document['id']}"

    response = await client.delete(url, headers=auth_headers)

    assert response.status_code == 204
    assert (await client.get(url, headers=auth_headers)).status_code == 404
    assert stored_files(storage) == []


async def test_delete_project_also_removes_its_files(
    client: AsyncClient,
    auth_headers: dict[str, str],
    project_id: str,
    storage: LocalStorage,
) -> None:
    other_project_id = await create_project(client, auth_headers)
    await upload(client, auth_headers, project_id, "a.md")
    await upload(client, auth_headers, project_id, "b.pdf", PDF)
    await upload(client, auth_headers, other_project_id, "se-queda.md")

    response = await client.delete(
        f"/api/v1/projects/{project_id}", headers=auth_headers
    )

    assert response.status_code == 204
    # Solo queda el archivo del otro proyecto.
    [remaining] = stored_files(storage)
    assert other_project_id in str(remaining)


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", ""), ("GET", f"/{uuid.uuid7()}"), ("DELETE", f"/{uuid.uuid7()}")],
)
async def test_documents_without_token_return_401(
    client: AsyncClient, method: str, path: str
) -> None:
    response = await client.request(
        method, f"/api/v1/projects/{uuid.uuid7()}/documents{path}"
    )

    assert response.status_code == 401
