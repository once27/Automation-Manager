"""Shared API error responses for control-plane requests."""

from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class APIError(Exception):
    """An expected API failure with a stable client-facing error code."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


async def api_error_handler(_request: Request, error: APIError) -> JSONResponse:
    """Return the documented safe error envelope and correlation ID."""
    request_id = str(uuid4())
    response = JSONResponse(
        status_code=error.status_code,
        content={
            "error": {
                "code": error.code,
                "message": error.message,
                "request_id": request_id,
                "details": error.details,
            }
        },
        headers=error.headers,
    )
    response.headers["X-Request-ID"] = request_id
    return response


async def validation_error_handler(
    _request: Request, error: RequestValidationError
) -> JSONResponse:
    """Return validation locations without echoing any submitted values."""
    request_id = str(uuid4())
    safe_errors = [
        {
            "location": [str(part) for part in item.get("loc", ())],
            "code": str(item.get("type", "invalid")),
        }
        for item in error.errors()
    ]
    response = JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "The request did not match the API contract.",
                "request_id": request_id,
                "details": {"errors": safe_errors},
            }
        },
    )
    response.headers["X-Request-ID"] = request_id
    return response


async def http_exception_handler(
    _request: Request, error: StarletteHTTPException
) -> JSONResponse:
    """Envelope framework HTTP failures without reflecting arbitrary details."""
    error_codes = {
        400: ("BAD_REQUEST", "The request could not be processed."),
        401: ("AUTHENTICATION_REQUIRED", "Authentication is required."),
        403: ("FORBIDDEN", "The request is not permitted."),
        404: ("NOT_FOUND", "The requested resource was not found."),
        405: ("METHOD_NOT_ALLOWED", "The method is not allowed for this resource."),
        409: ("CONFLICT", "The request conflicts with the current resource state."),
        410: ("GONE", "The requested resource is no longer available."),
        413: ("PAYLOAD_TOO_LARGE", "The request payload is too large."),
        415: ("UNSUPPORTED_MEDIA_TYPE", "The request media type is not supported."),
        429: ("RATE_LIMITED", "Too many requests were received."),
    }
    code, message = error_codes.get(
        error.status_code,
        (f"HTTP_{error.status_code}", "The request could not be completed."),
    )
    request_id = str(uuid4())
    response = JSONResponse(
        status_code=error.status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
                "details": None,
            }
        },
        headers=error.headers,
    )
    response.headers["X-Request-ID"] = request_id
    return response
