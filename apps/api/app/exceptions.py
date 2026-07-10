"""Global exception handler.

AI Prompt §4.4 + Master Spec §6.3: NEVER expose a stack trace to a client.
The user-facing 500 response carries only the correlation ID. Internal
diagnostics go to the structured log under the matching correlation ID so an
operator can join them.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.logging import get_logger

logger = get_logger(__name__)


def _correlation_id_from(request: Request) -> str:
    return getattr(request.state, "correlation_id", "unknown")


async def _handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.status_code,
                "message": exc.detail,
                "correlation_id": _correlation_id_from(request),
            }
        },
    )


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": 422,
                "message": "Request validation failed.",
                "details": exc.errors(),
                "correlation_id": _correlation_id_from(request),
            }
        },
    )


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    cid = _correlation_id_from(request)
    logger.exception(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        correlation_id=cid,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": 500,
                "message": "An internal error occurred. Please contact support.",
                "correlation_id": cid,
            }
        },
    )


async def _handle_redaction_ack_required(request: Request, exc: Exception) -> JSONResponse:
    """FIX H-6: live egress before anyone reviewed the redacted payload.

    A typed 409 with instructions, not a generic 500. The message tells the
    operator exactly what to do: preview, then acknowledge, then run.
    """
    cid = _correlation_id_from(request)
    return JSONResponse(
        status_code=409,
        content={"error": {"code": 409, "message": str(exc), "correlation_id": cid}},
    )


def register_exception_handlers(app: FastAPI) -> None:
    from app.ai.llm import RedactionAckRequiredError

    app.add_exception_handler(HTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    # Registered BEFORE the catch-all so it wins over _handle_unexpected.
    app.add_exception_handler(RedactionAckRequiredError, _handle_redaction_ack_required)
    app.add_exception_handler(Exception, _handle_unexpected)
