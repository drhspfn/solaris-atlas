import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, TimeoutError

logger = logging.getLogger(__name__)


class NotFoundError(Exception):
    """Raised when a canonical resource does not exist."""


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DBAPIError)
    @app.exception_handler(TimeoutError)
    async def database_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        # Do not expose SQL, bound values or connection credentials to the caller.
        code = getattr(getattr(exc, "orig", None), "sqlstate", None)
        retryable = isinstance(exc, TimeoutError) or (
            isinstance(exc, DBAPIError) and (exc.connection_invalidated or
                code in {"40001", "40P01", "55P03", "57014", "53300", "57P01"} or
                bool(code and code.startswith("08")))
        )
        status = 503 if retryable else 500
        logger.warning("api.database_error status=%s sqlstate=%s", status, code)
        return JSONResponse(
            status_code=status,
            content={"detail": "Archive database is temporarily unavailable"},
            headers={"Retry-After": "2", "Cache-Control": "no-store"},
        )

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
