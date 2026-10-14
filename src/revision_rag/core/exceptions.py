from typing import ClassVar


class AppError(Exception):
    """Error de negocio.

    Los servicios los lanzan sin saber nada de HTTP; un exception handler en
    main.py los traducirá a una respuesta con este status_code y detail.
    """

    status_code: int = 400
    detail: str = "Petición no válida"
    headers: ClassVar[dict[str, str] | None] = None


class EmailAlreadyRegisteredError(AppError):
    status_code = 409
    detail = "Ese email ya está registrado"


class InvalidCredentialsError(AppError):
    # El mismo mensaje si el email no existe o si la contraseña está mal: así
    # no se puede averiguar qué emails están registrados.
    status_code = 401
    detail = "Email o contraseña incorrectos"


class NotAuthenticatedError(AppError):
    status_code = 401
    detail = "No autenticado"
    # El estándar (RFC 6750) pide esta cabecera en los 401 de rutas protegidas
    # con token: indica al cliente cómo autenticarse.
    headers: ClassVar[dict[str, str] | None] = {"WWW-Authenticate": "Bearer"}


class InvalidRefreshTokenError(AppError):
    status_code = 401
    detail = "Refresh token no válido"


class ProjectNotFoundError(AppError):
    status_code = 404
    detail = "Proyecto no encontrado"
