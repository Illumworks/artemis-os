"""Memory keystone — two-tier evidence-linked store.

Public API lives in store.py. Models in models.py. Pydantic DTOs in schemas.py.

LOSSLESS RULE: drawers and observations are never deleted.
Observations leave active retrieval only via superseded_by.

Reference implementation: claudeck-artemis/server/memory-* and db/sqlite.js memory sections.
"""

from artemis.memory.schemas import (
    Drawer,
    Evidence,
    Observation,
    Scope,
    ScopeKind,
    ScopeRead,
    Source,
)
from artemis.memory.store import (
    get_drawer,
    get_observation,
    get_or_create_scope,
    link_evidence,
    list_evidence_for_observation,
    supersede_observation,
    write_drawer,
    write_observation,
)

__all__ = [
    # schemas
    "Drawer",
    "Evidence",
    "Observation",
    "Scope",
    "ScopeKind",
    "ScopeRead",
    "Source",
    # store
    "get_drawer",
    "get_observation",
    "get_or_create_scope",
    "link_evidence",
    "list_evidence_for_observation",
    "supersede_observation",
    "write_drawer",
    "write_observation",
]
