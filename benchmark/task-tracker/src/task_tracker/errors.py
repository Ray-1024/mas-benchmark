class TaskTrackerError(Exception):
    status_code = 400


class AuthenticationError(TaskTrackerError):
    status_code = 401


class AuthorizationError(TaskTrackerError):
    status_code = 403


class NotFoundError(TaskTrackerError):
    status_code = 404


class ConflictError(TaskTrackerError):
    status_code = 409


class ValidationError(TaskTrackerError):
    status_code = 422
