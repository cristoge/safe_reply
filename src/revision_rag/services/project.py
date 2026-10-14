import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.clients.storage import Storage, delete_files
from revision_rag.core.exceptions import ProjectNotFoundError
from revision_rag.models import Project, User
from revision_rag.repositories.document import DocumentRepository
from revision_rag.repositories.project import ProjectRepository
from revision_rag.schemas.project import ProjectCreate, ProjectUpdate


class ProjectService:
    def __init__(
        self,
        session: AsyncSession,
        projects: ProjectRepository,
        documents: DocumentRepository,
        storage: Storage,
    ) -> None:
        self.session = session
        self.projects = projects
        self.documents = documents
        self.storage = storage

    async def create(self, user: User, data: ProjectCreate) -> Project:
        project = Project(user_id=user.id, name=data.name, description=data.description)
        await self.projects.add(project)
        await self.session.commit()
        return project

    async def list_projects(
        self, user: User, *, limit: int, offset: int
    ) -> Sequence[Project]:
        return await self.projects.list_for_user(user.id, limit=limit, offset=offset)

    async def get(self, user: User, project_id: uuid.UUID) -> Project:
        """El proyecto, solo si es del usuario.

        404 tanto si no existe como si es de otro (y no 403): un 403 confirmaría
        que ese id existe. Es la regla que evita el IDOR.
        """
        project = await self.projects.get_for_user(project_id, user.id)
        if project is None:
            raise ProjectNotFoundError
        return project

    # update y delete reciben el proyecto ya comprobado con get(): no pueden
    # usarse con el proyecto de otro usuario.
    async def update(self, project: Project, data: ProjectUpdate) -> Project:
        # exclude_unset: solo los campos que vienen en el body (semántica de PATCH).
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(project, field, value)
        await self.session.commit()
        return project

    async def delete(self, project: Project) -> None:
        # Postgres borra las filas de los documentos (CASCADE), pero no sabe
        # que existen sus archivos: eso lo hace el servicio. Las claves se leen
        # antes, porque después del borrado ya no están.
        storage_keys = await self.documents.list_storage_keys(project.id)
        await self.projects.delete(project)
        await self.session.commit()
        await delete_files(self.storage, storage_keys)
