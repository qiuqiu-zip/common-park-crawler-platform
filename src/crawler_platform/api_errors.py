from __future__ import annotations

from typing import Any


class ApiError(RuntimeError):
    code = "INTERNAL_ERROR"
    status_code = 500

    def __init__(self, message: str, *, details: list[dict[str, Any]] | None = None) -> None:
        self.message = message
        self.details = details or []
        super().__init__(message)


class ValidationApiError(ApiError):
    code = "VALIDATION_ERROR"
    status_code = 422


class NotFoundApiError(ApiError):
    code = "NOT_FOUND"
    status_code = 404


class ConflictApiError(ApiError):
    code = "CONFLICT"
    status_code = 409


class InvalidStateApiError(ApiError):
    code = "INVALID_STATE"
    status_code = 409


class StorageApiError(ApiError):
    code = "STORAGE_ERROR"
    status_code = 500


class EngineApiError(ApiError):
    code = "ENGINE_ERROR"
    status_code = 500


class ExportApiError(ApiError):
    code = "EXPORT_ERROR"
    status_code = 400


class InternalApiError(ApiError):
    code = "INTERNAL_ERROR"
    status_code = 500
