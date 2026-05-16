"""FastAPI app entrypoint.

Run: `uv run uvicorn artemis.main:app --reload`
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from artemis import __version__
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

# API routes — must be mounted BEFORE StaticFiles so /api/* takes precedence.
app.include_router(health.router)

# Mount static frontend AFTER all API routes.
# html=True makes GET / serve public/index.html.
app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")
