"""FastAPI app entrypoint.

Run: `uv run uvicorn artemis.main:app --reload`
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from artemis import __version__
from artemis.marketing.routes import (
    approvals,
    campaign_deliverables,
    campaign_ops,
    content_assets,
    scouts,
    signal_criteria,
    signal_queue,
)
from artemis.routes import health

PUBLIC_DIR = Path(__file__).parent.parent / "public"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


app = FastAPI(
    title="Artemis OS",
    description="Marketing intelligence + campaign workflow system.",
    version=__version__,
    lifespan=lifespan,
)

# ── Error handlers ────────────────────────────────────────────────────────────
# Unwrap HTTPException.detail when it is already a dict so the wire shape
# matches the Node app's { error, code, details? } convention.


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    """Return detail dict directly (not wrapped in {"detail": ...})."""
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": str(exc.detail), "code": "http_error"},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Convert Pydantic validation errors to the Node-compatible shape."""
    details: dict[str, Any] = {"errors": exc.errors()}
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation failed",
            "code": "validation_error",
            "details": details,
        },
    )


# CORS — permissive defaults matching Node app behaviour
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes — must be mounted BEFORE StaticFiles so /api/* takes precedence.
app.include_router(health.router)

# Phase C2 — Marketing OS HTTP routes
app.include_router(scouts.router)
app.include_router(signal_queue.router)
app.include_router(signal_criteria.router)
app.include_router(campaign_ops.router)
app.include_router(campaign_deliverables.router)
app.include_router(content_assets.router)
app.include_router(approvals.router)

# Mount static frontend AFTER all API routes.
# html=True makes GET / serve public/index.html.
app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")
