"""Sanitized errors shared by the local workspace endpoints."""


class WorkspaceError(Exception):
    """An intentionally public, path-free error."""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        self.code, self.message, self.status = code, message, status
