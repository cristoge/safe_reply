from typing import ClassVar


class AppError(Exception):
    """Error de negocio.

    Los servicios los lanzan sin saber nada de HTTP; un exception handler en
    main.py los traducirá a una respuesta con este status_code y detail.
    """

    status_code: int = 400
    detail: str = "Petición no válida"
    headers: ClassVar[dict[str, str] | None] = None

    def __init__(self, detail: str | None = None) -> None:
        # El mensaje de la clase vale para casi todo; se puede dar uno concreto
        # cuando el mismo error tiene varias causas.
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)

    def response_headers(self) -> dict[str, str] | None:
        # Método y no solo el atributo: algunas cabeceras dependen de cada error
        # (el Retry-After del 429), no de la clase.
        return self.headers


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


class ForbiddenError(AppError):
    # 403 y no 401: el usuario está autenticado (sabemos quién es), pero su rol
    # no le permite hacer esto.
    status_code = 403
    detail = "No tienes permiso para hacer esto"


class TicketNotFoundError(AppError):
    status_code = 404
    detail = "Ticket no encontrado"


class PhoneRequiredError(AppError):
    # Si el ticket va a una persona, el banco llama al cliente. Solo les pasa a
    # usuarios creados antes de que el teléfono fuera obligatorio.
    status_code = 409
    detail = "Añade un teléfono a tu cuenta antes de abrir un ticket"


class RateLimitExceededError(AppError):
    status_code = 429
    detail = "Demasiados tickets en poco tiempo; inténtalo más tarde"

    def __init__(self, retry_after: int) -> None:
        super().__init__()
        self.retry_after = retry_after

    def response_headers(self) -> dict[str, str]:
        # Segundos hasta que se reinicia la ventana: el cliente sabe cuándo
        # puede volver a intentarlo sin adivinar.
        return {"Retry-After": str(self.retry_after)}


class IdempotencyKeyReusedError(AppError):
    # La misma key con otro contenido es un error del cliente: devolver el
    # ticket anterior escondería que ha mandado otra cosa.
    status_code = 422
    detail = "Esa Idempotency-Key ya se usó con otro contenido"
