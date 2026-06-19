"""Argus — Marketing Research Agent.

Argus Panoptes, the hundred-eyed giant. He researches K-12 districts in depth
and persists findings into the district memory drawer so Callie's briefings and
campaigns automatically carry that context.

Public API:
    from artemis.argus import research_district
    from artemis.argus import read_district_drawer, write_district_findings

Drawer convention:
    scope_kind = "workspace"
    scope_id   = "marketing"
    category   = "district_research"
    Observations are keyed by district_key (district id or normalised name).
    Each finding carries source="Argus" + provenance in structured content.

Writes go through write_observation (store.py) so content-hash dedup and the
incremental consolidator's conflict detection apply automatically.  Argus does
NOT reimplement dedup — it rides the memory layer.
"""

from artemis.argus.drawer import read_district_drawer, write_district_findings

__all__ = [
    "read_district_drawer",
    "write_district_findings",
    "research_district",
]


def __getattr__(name: str):
    # Lazy-load research_district to break a circular import: flow -> research ->
    # providers.fallback -> claude_code.adapter, which (via mcp_server ->
    # tool_registry -> argus_tools -> argus.models -> this __init__) re-enters the
    # half-initialized adapter at import time. Deferring to attribute access means
    # providers are fully initialized by the time this resolves.
    if name == "research_district":
        from artemis.argus.flow import research_district

        return research_district
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
