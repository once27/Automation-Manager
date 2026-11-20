"""FastAPI application entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from browserfleet_api.db.config import get_database_url
from browserfleet_api.db.session import create_engine, create_session_factory
from browserfleet_api.errors import (
    APIError,
    api_error_handler,
    http_exception_handler,
    validation_error_handler,
)
from browserfleet_api.jobs.router import router as jobs_router
from browserfleet_api.workflows import router as workflows_router


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Own the database engine for the API process lifetime."""
    engine = create_engine(get_database_url())
    application.state.session_factory = create_session_factory(engine)
    try:
        yield
    finally:
        engine.dispose()


app = FastAPI(
    title="BrowserFleet API",
    version="0.1.0",
    lifespan=lifespan,
    exception_handlers={
        APIError: api_error_handler,
        RequestValidationError: validation_error_handler,
        StarletteHTTPException: http_exception_handler,
    },
)

app.include_router(workflows_router)
app.include_router(jobs_router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Report that the API process is responding."""
    return {"status": "ok"}
