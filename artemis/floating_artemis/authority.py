"""Authority layer enforcement for Floating Artemis tools.

Four layers:
  1 — Read-only queries: invoked directly, no approval needed.
  2 — Idempotent writes / safe side-effects: invoked directly.
  3 — Side-effect writes: emits tool_pending WS event, waits for /tool-confirm.
  4 — Destructive / irreversible: same as layer 3 but treated with higher urgency.

Layer 1+2 tools run during the agent loop without pause.
Layer 3+4 tools cause the loop to yield: the tool_use block is stored with
role=assistant, a WS event is broadcast, and execution suspends until the
operator POSTs to /tool-confirm with decision=run|cancel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from artemis.agent.types import Tool, ToolImpl

# Authority layer type alias for clarity.
AuthorityLayer = int  # 1, 2, 3, or 4


@dataclass(slots=True)
class AuthorizedToolEntry:
    """Tool definition + implementation + authority layer."""

    tool: Tool
    impl: ToolImpl
    layer: AuthorityLayer


class AuthorizedToolRegistry:
    """Registry that pairs Tool definitions with their implementations and authority layers."""

    def __init__(self) -> None:
        self._entries: dict[str, AuthorizedToolEntry] = {}

    def register(
        self,
        tool: Tool,
        impl: ToolImpl,
        *,
        layer: AuthorityLayer = 1,
    ) -> None:
        if tool.name in self._entries:
            raise ValueError(f"tool {tool.name!r} already registered")
        self._entries[tool.name] = AuthorizedToolEntry(tool=tool, impl=impl, layer=layer)

    def get(self, name: str) -> AuthorizedToolEntry | None:
        return self._entries.get(name)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def all_entries(self) -> list[AuthorizedToolEntry]:
        return list(self._entries.values())

    def specs(self) -> list[Tool]:
        """Return tool definitions for the model. Filtered to layer 1+2 for auto-invoke,
        but all tools are exposed to the model (layer determines how results are handled)."""
        return [e.tool for e in self._entries.values()]

    def is_auto_invoke(self, name: str) -> bool:
        """Return True if this tool runs immediately (layers 1 and 2)."""
        entry = self._entries.get(name)
        return entry is not None and entry.layer <= 2

    def requires_confirmation(self, name: str) -> bool:
        """Return True if this tool requires operator confirmation (layers 3 and 4)."""
        entry = self._entries.get(name)
        return entry is not None and entry.layer >= 3

    def filter_by_surfaces(self, available_surfaces: set[str]) -> AuthorizedToolRegistry:
        """Return a new registry with only tools whose surfaces are available.

        Tools without a declared surface tag always pass through.
        Surface tag is stored in the tool description as [surface:name].
        """
        filtered = AuthorizedToolRegistry()
        for entry in self._entries.values():
            surface = _extract_surface_tag(entry.tool.description)
            if surface is None or surface in available_surfaces:
                filtered._entries[entry.tool.name] = entry
        return filtered


def _extract_surface_tag(description: str) -> str | None:
    """Extract [surface:name] from a tool description string."""
    import re

    match = re.search(r"\[surface:([^\]]+)\]", description)
    return match.group(1) if match else None


# ── Pending-confirmation store (in-memory per process) ───────────────────────
# In a multi-worker deployment, this would live in Redis or the DB.
# For V1 single-process server, in-memory is sufficient.


@dataclass
class PendingConfirmation:
    """A layer-3/4 tool call awaiting operator confirmation."""

    session_id: str
    tool_use_id: str
    tool_name: str
    tool_input: dict[str, Any]
    layer: AuthorityLayer
    # asyncio.Event set when operator POSTs /tool-confirm
    event: Any = field(default_factory=lambda: None)
    decision: str | None = None  # "run" or "cancel"


class ConfirmationStore:
    """In-memory store of pending tool confirmations keyed by tool_use_id."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingConfirmation] = {}

    def add(self, confirmation: PendingConfirmation) -> None:
        self._pending[confirmation.tool_use_id] = confirmation

    def get(self, tool_use_id: str) -> PendingConfirmation | None:
        return self._pending.get(tool_use_id)

    def resolve(self, tool_use_id: str, decision: str) -> PendingConfirmation | None:
        pending = self._pending.pop(tool_use_id, None)
        if pending is not None:
            pending.decision = decision
        return pending

    def list_for_session(self, session_id: str) -> list[PendingConfirmation]:
        return [p for p in self._pending.values() if p.session_id == session_id]

    def clear_session(self, session_id: str) -> None:
        to_remove = [tid for tid, p in self._pending.items() if p.session_id == session_id]
        for tid in to_remove:
            del self._pending[tid]


# Module-level singleton.
confirmation_store = ConfirmationStore()
