class AppError(Exception):
    """Error de negocio.

    Los servicios los lanzan sin saber nada de HTTP; un exception handler en
    main.py los traducirá a una respuesta con este status_code y detail.
    """

    status_code: int = 400
    detail: str = "Petición no válida"


class EmailAlreadyRegisteredError(AppError):
    status_code = 409
    detail = "Ese email ya está registrado"
