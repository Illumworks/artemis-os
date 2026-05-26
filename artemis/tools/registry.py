"""Global tool factory registry.

Tool modules call ``register_tool(name, factory)`` at import time.
``artemis/tools/__init__.py`` imports every submodule so side-effects fire on
``import artemis.tools``.
"""

from __future__ import annotations

from collections.abc import Callable

from artemis.agent.types import Tool, ToolImpl
from artemis.tools.context import ToolContext

#: A factory takes a ToolContext and returns (Tool definition, async impl).
ToolFactory = Callable[[ToolContext], tuple[Tool, ToolImpl]]

_TOOL_FACTORIES: dict[str, ToolFactory] = {}


def register_tool(name: str, factory: ToolFactory) -> None:
    """Register a tool factory globally. Raises if name is already taken."""
    if name in _TOOL_FACTORIES:
        raise ValueError(f"tool {name!r} already registered")
    _TOOL_FACTORIES[name] = factory


def get_factory(name: str) -> ToolFactory | None:
    return _TOOL_FACTORIES.get(name)


def known_tool_names() -> tuple[str, ...]:
    return tuple(sorted(_TOOL_FACTORIES))
