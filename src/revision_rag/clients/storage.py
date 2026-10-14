import asyncio
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class Storage(Protocol):
    """Dónde se guardan los archivos subidos. Los servicios solo conocen esto.

    Las claves son rutas relativas ("projects/<id>/<id>.pdf"). Hoy se guardan en
    disco (LocalStorage); con S3 sería otra clase con los mismos métodos, y los
    servicios no se enterarían.
    """

    async def save(self, key: str, data: bytes) -> None: ...

    async def delete(self, key: str) -> None:
        """Borra el archivo. Si no existe, no hace nada."""
        ...


class LocalStorage:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir.resolve()

    def _path(self, key: str) -> Path:
        # Las claves las genera la app, pero por si acaso: una clave con ".."
        # no puede escribir ni borrar fuera de la carpeta del storage.
        path = (self.base_dir / key).resolve()
        if not path.is_relative_to(self.base_dir):
            raise ValueError(f"Clave fuera del storage: {key!r}")
        return path

    # Escribir y borrar en disco bloquea: se hace en otro hilo (como argon2).
    async def save(self, key: str, data: bytes) -> None:
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(write)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)


async def delete_files(storage: Storage, keys: Iterable[str]) -> None:
    """Borra archivos después de haber borrado sus filas en la BD.

    Si falla alguno, solo se avisa en el log: la fila ya no existe, así que un
    archivo huérfano no rompe nada (al revés, una fila sin archivo, sí).
    """
    for key in keys:
        try:
            await storage.delete(key)
        except OSError:
            logger.warning("No se pudo borrar el archivo %s del storage", key)
