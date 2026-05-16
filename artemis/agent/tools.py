"""Tool registry — pairs `Tool` definitions with their async implementations."""

from __future__ import annotations

from dataclasses import dataclass, field

from artemis.agent.types import Tool, ToolImpl


@dataclass(slots=True)
class ToolEntry:
    tool: Tool
    impl: ToolImpl


@dataclass(slots=True)
class ToolRegistry:
    _entries: dict[str, ToolEntry] = field(default_factory=dict)

    def register(self, tool: Tool, impl: ToolImpl) -> None:
        if tool.name in self._entries:
            raise ValueError(f"tool {tool.name!r} already registered")
        self._entries[tool.name] = ToolEntry(tool=tool, impl=impl)

    def get(self, name: str) -> ToolEntry | None:
        return self._entries.get(name)

    def specs(self) -> list[Tool]:
        """Return tool definitions, model-facing. Order is registration order."""
        return [e.tool for e in self._entries.values()]

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._entries
