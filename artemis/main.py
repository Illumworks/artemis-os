"""FastAPI app entrypoint.

Run: `uv run uvicorn artemis.main:app --reload`

Note: env files are loaded in `artemis/__init__.py` on package import, before
any other module reads `os.environ`.
"""

import asyncio
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
from artemis.automations.routes import router as automations_router
from artemis.automations.scheduler import (
    start_automation_scheduler,
    stop_automation_scheduler,
)
from artemis.builder.routes import agents_subresource_router as builder_agents_router
from artemis.builder.routes import router as builder_router
from artemis.config import settings
from artemis.connectors.routes import agents_router as connectors_agents_router
from artemis.connectors.routes import router as connectors_router
from artemis.integrations.token_refresh.scheduler import (
    start_token_refresh_scheduler,
    stop_token_refresh_scheduler,
)
from artemis.marketing.routes import (
    approvals,
    campaign_cost,
    campaign_deliverables,
    campaign_ops,
    claims,
    comments,
    content_assets,
    districts,
    initiation,
    intel_prioritization,
    scouts,
    sends,
    signal_criteria,
    signal_queue,
    templates,
    writing_studio,
)
from artemis.marketing.scout_scheduler import start_scout_scheduler, stop_scout_scheduler
from artemis.marketing.writing_studio import adapter as ws_adapter
from artemis.marketing.writing_studio import events as ws_events
from artemis.marketing.writing_studio.collab.routes import router as ws_collab_router
from artemis.marketing.writing_studio.collab.runtime_guard import (
    warn_if_multiworker_collab,
)
from artemis.meetings.scheduler import start_meeting_scheduler, stop_meeting_scheduler
from artemis.memory.scheduler import start_memory_scheduler, stop_memory_scheduler
from artemis.pipelines.routes import router as pipelines_router
from artemis.pipelines.scheduler import (
    start_pipeline_scheduler,
    stop_pipeline_scheduler,
)
from artemis.proactivity.scheduler import (
    start_proactivity_scheduler,
    stop_proactivity_scheduler,
)
from artemis.routes import calendar as calendar_routes
from artemis.routes import costs as costs_routes
from artemis.routes import costs_routing as costs_routing_routes
from artemis.routes import daily_brief as daily_brief_routes
from artemis.routes import dev_projects as dev_projects_routes
from artemis.routes import enablement as enablement_routes
from artemis.routes import gmail as gmail_routes
from artemis.routes import google_docs as google_docs_routes
from artemis.routes import health, okr, parallel, status, writing_rules, writing_studio_tags
from artemis.routes import jira as jira_routes
from artemis.routes import me as me_routes
from artemis.routes import meetings as meetings_routes
from artemis.routes import notifications as notifications_routes
from artemis.routes import people as people_routes
from artemis.routes import sessions as sessions_routes
from artemis.routes import stats as stats_routes
from artemis.routes import users as users_routes
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
from artemis.routes.memory import router as memory_router
from artemis.routes.routing import router as routing_router
from artemis.routes.slack import router as slack_router
from artemis.ws.routes import router as ws_router

PUBLIC_DIR = Path(__file__).parent.parent / "public"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    warn_if_multiworker_collab(settings.uvicorn_workers)
    # --- TEMP DIAG: event-loop freeze capture for the asyncpg/instability bug.
    # Remove with artemis/loop_diag.py once the bug is closed. ---
    from artemis.loop_diag import install as _install_loop_diag

    _install_loop_diag()
    # --- END TEMP DIAG ---
    # Wait for Postgres to be ready before starting any schedulers.  On Mac
    # wake-from-sleep / Postgres cold-start the DB may not accept connections
    # for several seconds; schedulers that fire immediately against a dead pool
    # produce cascading job failures and can leave the app unresponsive.
    # Imported lazily to avoid triggering any circular-import cycles at
    # module load time.
    from artemis.db import wait_for_db_ready

    await wait_for_db_ready()
    # Subscribe the Writing Studio adapter to draft lifecycle events.
    ws_adapter.init_adapter()
    # Start the meeting auto-summarizer scheduler.
    start_meeting_scheduler()
    # Start the proactive OAuth token refresh scheduler (J10e).
    start_token_refresh_scheduler()
    # Start the automation cron scheduler (OP1).
    start_automation_scheduler()
    # Start the scout execution scheduler (M5b).
    start_scout_scheduler()
    # Start the pipeline execution scheduler (PIPE4).
    start_pipeline_scheduler()
    # Start daily memory maintenance (quick-win gap #4).
    start_memory_scheduler()
    # Start proactive morning-brief delivery (P2a).
    start_proactivity_scheduler()
    # Recover any Argus research requests orphaned by a previous process restart.
    # Non-blocking: fires background tasks and returns immediately.
    from artemis.floating_artemis.tools.argus_tools import recover_pending_requests
    asyncio.create_task(recover_pending_requests())
    try:
        yield
    finally:
        # Unsubscribe the adapter on shutdown so tests / restarts start clean.
        ws_adapter.reset_adapter()
        ws_events.clear_subscribers()
        # Stop the schedulers before process exit.
        stop_meeting_scheduler()
        stop_token_refresh_scheduler()
        stop_automation_scheduler()
        stop_scout_scheduler()
        stop_pipeline_scheduler()
        stop_memory_scheduler()
        stop_proactivity_scheduler()


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
    """Convert Pydantic validation errors to the Node-compatible shape.

    Pydantic V2 errors include a ValueError in ctx.error which is not JSON
    serializable. Strip ctx from errors before serializing.
    """

    def _clean_error(e: dict[str, Any]) -> dict[str, Any]:
        cleaned = {k: v for k, v in e.items() if k != "ctx"}
        if "ctx" in e:
            ctx = e["ctx"]
            cleaned["ctx"] = {k: str(v) if isinstance(v, Exception) else v for k, v in ctx.items()}
        return cleaned

    details: dict[str, Any] = {"errors": [_clean_error(e) for e in exc.errors()]}
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
app.include_router(me_routes.router)
app.include_router(users_routes.router)

# Phase C2 — Marketing OS HTTP routes
app.include_router(scouts.router)
app.include_router(signal_queue.router)
app.include_router(signal_criteria.router)
app.include_router(campaign_ops.router)
app.include_router(campaign_cost.router)
app.include_router(initiation.router)
app.include_router(initiation.initiation_extras_router)
app.include_router(districts.router)
app.include_router(campaign_deliverables.router)
app.include_router(content_assets.router)
app.include_router(approvals.router)
app.include_router(sends.router)
app.include_router(claims.router)
app.include_router(comments.router)
app.include_router(templates.router)
app.include_router(writing_studio.router)
app.include_router(ws_collab_router)
app.include_router(intel_prioritization.router)

# Phase F2a — Builders backend CRUD (agents, skills, workflows, chains, DAGs)
app.include_router(agents.router)
app.include_router(agent_runs.router)
app.include_router(skills.router)
app.include_router(workflows.router)
app.include_router(agent_chains.router)
app.include_router(agent_dags.router)

# Phase F2b — Execution wiring (run agents / workflows / chains / DAGs)
app.include_router(execution.router)

# O1 — Agent-Builder + Self-Improvement
app.include_router(builder_router)
app.include_router(builder_agents_router)

# Phase E2 — WebSocket relay for live run streaming
app.include_router(ws_router)

# Memory M2 — conflict management + observation history routes
app.include_router(memory_router)

# Phase H — OKR Studio + Writing Studio rules (dry-run + validator shipped; cutover pending)
app.include_router(okr.router)
app.include_router(writing_rules.router)
app.include_router(writing_studio_tags.router)
app.include_router(google_docs_routes.router)

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
app.include_router(gmail_routes.router)
app.include_router(meetings_routes.router)
app.include_router(meetings_routes.granola_compat_router)
# J6c — personal todos
app.include_router(meetings_routes.todos_router)

# People search — merged Google Contacts + Slack users (attendee autocomplete)
app.include_router(people_routes.router)

# J7 — Daily brief
app.include_router(daily_brief_routes.router)

# Enablement indexing — Apps Script ingest webhook (feeds Kai's enablement_assets)
app.include_router(enablement_routes.router)

# OP1 — Automations registry
app.include_router(automations_router)

# Connectors — per-source credential management
app.include_router(connectors_router)
app.include_router(connectors_agents_router)

# PIPE1 — Pipelines data model + CRUD
app.include_router(pipelines_router)

# Phase R — Routing control surface (provider health, feature overrides, audit log)
app.include_router(routing_router)

# Cost Phase 2 — visibility dashboard rollup endpoint
app.include_router(costs_routes.router)

# Cost Phase 3 — routing opportunities + Apply-button backend
app.include_router(costs_routing_routes.router)

# J3c stubs — Jira overview, sessions, notifications, stats
app.include_router(jira_routes.router)
app.include_router(sessions_routes.router)
app.include_router(notifications_routes.router)
app.include_router(stats_routes.router)


# Mount static frontend AFTER all API routes.
# html=True makes GET / serve public/index.html.
app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")
