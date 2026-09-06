"""Errors raised by application use cases."""


class ApplicationError(Exception):
    def __init__(self, *, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class NotFoundError(ApplicationError):
    def __init__(self, resource: str, resource_id: str) -> None:
        super().__init__(
            code="not_found",
            message=f"{resource} '{resource_id}' was not found",
            status_code=404,
        )


class ConflictError(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="conflict", message=message, status_code=409)
