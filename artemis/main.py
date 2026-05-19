"""FastAPI app entrypoint.

Run: `uv run uvicorn artemis.main:app --reload`

Note: env files are loaded in `artemis/__init__.py` on package import, before
any other module reads `os.environ`.
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
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from artemis import __version__
from artemis.marketing.routes import (
    approvals,
    campaign_deliverables,
    campaign_ops,
    content_assets,
    scouts,
    signal_criteria,
    signal_queue,
    writing_studio,
)
from artemis.marketing.writing_studio import adapter as ws_adapter
from artemis.marketing.writing_studio import events as ws_events
from artemis.meetings.scheduler import start_meeting_scheduler, stop_meeting_scheduler
from artemis.routes import calendar as calendar_routes
from artemis.routes import daily_brief as daily_brief_routes
from artemis.routes import dev_projects as dev_projects_routes
from artemis.routes import health, okr, parallel, status, writing_rules
from artemis.routes import jira as jira_routes
from artemis.routes import meetings as meetings_routes
from artemis.routes import notifications as notifications_routes
from artemis.routes import people as people_routes
from artemis.routes import sessions as sessions_routes
from artemis.routes import stats as stats_routes
from artemis.routes.builders import (
    agent_chains,
    agent_dags,
    agent_runs,
    agents,
    execution,
    skills,
    workflows,
)
from artemis.routes.floating_artemis import router as fa_router
from artemis.routes.floating_artemis import ws_router as fa_ws_router
from artemis.routes.integrations import router as integrations_router
from artemis.routes.integrations_slack_events import router as slack_events_router
from artemis.routes.slack import router as slack_router
from artemis.ws.routes import router as ws_router

PUBLIC_DIR = Path(__file__).parent.parent / "public"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Subscribe the Writing Studio adapter to draft lifecycle events.
    ws_adapter.init_adapter()
    # Start the meeting auto-summarizer scheduler.
    start_meeting_scheduler()
    try:
        yield
    finally:
        # Unsubscribe the adapter on shutdown so tests / restarts start clean.
        ws_adapter.reset_adapter()
        ws_events.clear_subscribers()
        # Stop the scheduler before process exit.
        stop_meeting_scheduler()


class TrailingSlashCompatMiddleware:
    """ASGI middleware that retries /api/* requests with a trailing slash on 404.

    Why: FastAPI's built-in ``redirect_slashes=True`` is inert here because the
    ``StaticFiles(html=True)`` mount at ``/`` provides a full route match for
    *every* HTTP path, so Starlette's redirect-slash logic (which only fires
    when *no* route matches) is never reached.  This middleware fills the gap
    for API paths without the overhead of a real HTTP redirect round-trip: the
    retry is an in-process ASGI re-dispatch, so the browser/frontend sees a
    direct 200 (or 422) with no extra network hop.

    Only acts on GET requests to ``/api/*`` that lack a trailing slash and
    return 404.  WebSocket upgrades, non-API paths, and already-slashed paths
    are passed through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") not in ("GET", "HEAD")
            or not scope.get("path", "").startswith("/api/")
            or scope.get("path", "").endswith("/")
        ):
            await self.app(scope, receive, send)
            return

        # First attempt: capture the status code.
        status_holder: list[int] = []
        headers_holder: list[list[tuple[bytes, bytes]]] = []
        body_parts: list[bytes] = []

        async def capture_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder.append(message["status"])
                headers_holder.append(message.get("headers", []))
            elif message["type"] == "http.response.body":
                body_parts.append(message.get("body", b""))

        await self.app(scope, receive, capture_send)

        if status_holder and status_holder[0] != 404:
            # Not a 404 — replay the captured response as-is.
            await send(
                {
                    "type": "http.response.start",
                    "status": status_holder[0],
                    "headers": headers_holder[0] if headers_holder else [],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"".join(body_parts),
                }
            )
            return

        # 404 on a /api/ path without trailing slash — retry with slash.
        slashed_scope = dict(scope)
        original_path: str = scope["path"]
        slashed_scope["path"] = original_path + "/"
        # raw_path must match path (bytes)
        slashed_scope["raw_path"] = (original_path + "/").encode()

        await self.app(slashed_scope, receive, send)


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

# Trailing-slash compatibility for /api/* list endpoints.
# Must be added AFTER CORS so it sits closer to the transport layer and sees the
# final status code from the inner app (CORS headers are added on the way out).
app.add_middleware(TrailingSlashCompatMiddleware)


# No-cache for HTML/JS/CSS/JSON during active development so Cloudflare and
# browsers don't serve stale bundles. Static binary assets (icons/fonts)
# are unaffected.
@app.middleware("http")
async def _no_cache_for_app_assets(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    path = request.url.path
    if path.endswith((".js", ".css", ".html", ".json")) or path == "/":
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


# API routes — must be mounted BEFORE StaticFiles so /api/* takes precedence.
app.include_router(health.router)
app.include_router(status.router)  # Phase E1b — surface availability bootstrap

# Phase C2 — Marketing OS HTTP routes
app.include_router(scouts.router)
app.include_router(signal_queue.router)
app.include_router(signal_criteria.router)
app.include_router(campaign_ops.router)
app.include_router(campaign_deliverables.router)
app.include_router(content_assets.router)
app.include_router(approvals.router)
app.include_router(writing_studio.router)

# Phase F2a — Builders backend CRUD (agents, skills, workflows, chains, DAGs)
app.include_router(agents.router)
app.include_router(agent_runs.router)
app.include_router(skills.router)
app.include_router(workflows.router)
app.include_router(agent_chains.router)
app.include_router(agent_dags.router)

# Phase F2b — Execution wiring (run agents / workflows / chains / DAGs)
app.include_router(execution.router)

# Phase E2 — WebSocket relay for live run streaming
app.include_router(ws_router)

# Phase H — OKR Studio + Writing Studio rules (dry-run + validator shipped; cutover pending)
app.include_router(okr.router)
app.include_router(writing_rules.router)

# Phase G1 — Floating Artemis backend (sessions, tools, authority, chat)
app.include_router(fa_router)
app.include_router(fa_ws_router)
app.include_router(parallel.router)  # B6 — parallel chat pane session allocation
app.include_router(dev_projects_routes.router)
app.include_router(dev_projects_routes.ws_router)

# Phase J1 — Slack integration (OAuth, CRUD, events)
app.include_router(integrations_router)
app.include_router(slack_events_router)

# J8 — Slack signals (Focus Rail card)
app.include_router(slack_router)

# Phase J3b — Calendar + Meetings overview endpoints
app.include_router(calendar_routes.router)
app.include_router(meetings_routes.router)
app.include_router(meetings_routes.granola_compat_router)
# J6c — personal todos
app.include_router(meetings_routes.todos_router)

# People search — merged Google Contacts + Slack users (attendee autocomplete)
app.include_router(people_routes.router)

# J7 — Daily brief
app.include_router(daily_brief_routes.router)

# J3c stubs — Jira overview, sessions, notifications, stats
app.include_router(jira_routes.router)
app.include_router(sessions_routes.router)
app.include_router(notifications_routes.router)
app.include_router(stats_routes.router)


# Mount static frontend AFTER all API routes.
# html=True makes GET / serve public/index.html.
app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")
