"""Status router — /api/_status.

Returns a machine-readable inventory of which UI surfaces have a Python
backend in this build.  The frontend uses this as a bootstrap check to
gate non-ported surfaces without showing runtime errors to the user.

No auth required — it is intentionally a public, zero-state endpoint.
"""

from fastapi import APIRouter

router = APIRouter(tags=["status"])

# Hand-maintained inventory of which UI surfaces have a Python backend
_AVAILABLE_SURFACES = {
    "scouts",
    "signal-queue",
    "signal-criteria",
    "campaign-ops",
    "campaign-deliverables",
    "content-assets",
    "approvals",
    "writing-studio",
    # Phase F2a — Builders backend CRUD
    "agents",
    "skills",
    "workflows",
    "agent-chains",
    "agent-dags",
    "agent-runs",
    # Phase H — OKR Studio + Writing Studio rules backend ported
    "okr",
    "writing-rules",
    # Phase G1 — Floating Artemis backend
    "floating-artemis",
    # Dev Projects rebuild — project-scoped Claude Code / Codex style sessions
    "dev-projects",
    # Phase J1 — Integrations (Slack live; Cal/Gmail/Jira/Granola pending)
    "integrations",
    # Phase J5 — Jira board backend ported
    "jira-board",
    # Phase J6a — Granola meeting notes integration
    "meetings",
}
_UNAVAILABLE_SURFACES = {
    # Node-only surfaces — backends not ported yet.
    "sessions",
    "projects",
    "chat",
    "memory-shell",
    # "okr-studio" removed — superseded by "okr" surface above
    "dags",
    "voice",
    "telegram",
    "cost-dashboard",
    "analytics",
}


@router.get("/api/_status")
async def get_status() -> dict[str, object]:
    return {
        "version": "0.0.1",
        "available_surfaces": sorted(_AVAILABLE_SURFACES),
        "unavailable_surfaces": sorted(_UNAVAILABLE_SURFACES),
        "available": True,
    }
