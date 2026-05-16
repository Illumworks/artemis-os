"""FastAPI app entrypoint.

Run: `uv run uvicorn artemis.main:app --reload`
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from artemis import __version__
from artemis.config import settings
from artemis.routes import health


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


app = FastAPI(
    title="Artemis OS",
    description="Marketing intelligence + campaign workflow system.",
    version=__version__,
    lifespan=lifespan,
)

app.include_router(health.router)


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "name": "Artemis OS",
        "version": __version__,
        "env": settings.env,
    }
