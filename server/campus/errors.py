class Problem(Exception):
    def __init__(self, code: str, message: str, status: int = 400, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable


def missing():
    return Problem("NOT_FOUND", "The requested resource was not found", 404)


def forbidden():
    return Problem("FORBIDDEN", "This credential cannot perform that operation", 403)


def conflict(message):
    return Problem("IDEMPOTENCY_CONFLICT", message, 409)
