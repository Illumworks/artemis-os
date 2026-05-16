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
}
_UNAVAILABLE_SURFACES = {
    # Node-only surfaces — backends not ported yet.
    "sessions",
    "agents",
    "projects",
    "chat",
    "memory-shell",
    "okr-studio",
    "jira-board",
    "skills",
    "workflows",
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
