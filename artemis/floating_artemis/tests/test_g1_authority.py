"""Tests for authority layer enforcement."""

from __future__ import annotations

from typing import Any

import pytest

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import (
    AuthorizedToolRegistry,
    ConfirmationStore,
    PendingConfirmation,
)

pytestmark = pytest.mark.asyncio


# ── Registry construction ─────────────────────────────────────────────────────


def _make_tool(name: str) -> Tool:
    return Tool(
        name=name,
        description=f"Tool {name}",
        input_schema={"type": "object", "properties": {}, "required": []},
    )


async def _noop(inp: dict[str, Any]) -> str:
    return "ok"


def test_register_and_get() -> None:
    reg = AuthorizedToolRegistry()
    t = _make_tool("t1")
    reg.register(t, _noop, layer=1)
    assert "t1" in reg
    assert reg.get("t1") is not None


def test_register_duplicate_raises() -> None:
    reg = AuthorizedToolRegistry()
    t = _make_tool("dup")
    reg.register(t, _noop, layer=1)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(t, _noop, layer=1)


def test_len() -> None:
    reg = AuthorizedToolRegistry()
    assert len(reg) == 0
    reg.register(_make_tool("a"), _noop, layer=1)
    reg.register(_make_tool("b"), _noop, layer=2)
    assert len(reg) == 2


def test_specs_returns_all_tools() -> None:
    reg = AuthorizedToolRegistry()
    reg.register(_make_tool("read"), _noop, layer=1)
    reg.register(_make_tool("write"), _noop, layer=3)
    specs = reg.specs()
    assert len(specs) == 2
    names = {s.name for s in specs}
    assert names == {"read", "write"}


# ── Layer checks ──────────────────────────────────────────────────────────────


def test_layer1_is_auto_invoke() -> None:
    reg = AuthorizedToolRegistry()
    reg.register(_make_tool("r"), _noop, layer=1)
    assert reg.is_auto_invoke("r")
    assert not reg.requires_confirmation("r")


def test_layer2_is_auto_invoke() -> None:
    reg = AuthorizedToolRegistry()
    reg.register(_make_tool("w"), _noop, layer=2)
    assert reg.is_auto_invoke("w")
    assert not reg.requires_confirmation("w")


def test_layer3_requires_confirmation() -> None:
    reg = AuthorizedToolRegistry()
    reg.register(_make_tool("s"), _noop, layer=3)
    assert not reg.is_auto_invoke("s")
    assert reg.requires_confirmation("s")


def test_layer4_requires_confirmation() -> None:
    reg = AuthorizedToolRegistry()
    reg.register(_make_tool("d"), _noop, layer=4)
    assert not reg.is_auto_invoke("d")
    assert reg.requires_confirmation("d")


def test_unknown_tool_not_auto_invoke() -> None:
    reg = AuthorizedToolRegistry()
    assert not reg.is_auto_invoke("nonexistent")
    assert not reg.requires_confirmation("nonexistent")


# ── Layer-1/2 tools execute via impl directly ─────────────────────────────────


async def test_layer1_impl_executes() -> None:
    reg = AuthorizedToolRegistry()

    async def impl(inp: dict[str, Any]) -> str:
        return f"result:{inp.get('x', '')}"

    t = Tool(
        name="compute",
        description="compute",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": []},
    )
    reg.register(t, impl, layer=1)
    entry = reg.get("compute")
    assert entry is not None
    out = await entry.impl({"x": "42"})
    assert out == "result:42"


async def test_layer2_impl_executes() -> None:
    reg = AuthorizedToolRegistry()
    called = []

    async def write_impl(inp: dict[str, Any]) -> str:
        called.append(inp)
        return "written"

    t = _make_tool("write")
    reg.register(t, write_impl, layer=2)
    entry = reg.get("write")
    assert entry is not None
    out = await entry.impl({"data": "x"})
    assert out == "written"
    assert called == [{"data": "x"}]


# ── ConfirmationStore ─────────────────────────────────────────────────────────


def test_confirmation_store_add_get() -> None:
    store = ConfirmationStore()
    pending = PendingConfirmation(
        session_id="s1",
        tool_use_id="tuid-1",
        tool_name="propose_agent",
        tool_input={"name": "my-agent"},
        layer=3,
    )
    store.add(pending)
    got = store.get("tuid-1")
    assert got is not None
    assert got.tool_name == "propose_agent"


def test_confirmation_store_resolve_run() -> None:
    store = ConfirmationStore()
    pending = PendingConfirmation(
        session_id="s1",
        tool_use_id="tuid-2",
        tool_name="propose_agent",
        tool_input={},
        layer=3,
    )
    store.add(pending)
    resolved = store.resolve("tuid-2", "run")
    assert resolved is not None
    assert resolved.decision == "run"
    # Should be removed from store
    assert store.get("tuid-2") is None


def test_confirmation_store_resolve_cancel() -> None:
    store = ConfirmationStore()
    pending = PendingConfirmation(
        session_id="s1",
        tool_use_id="tuid-3",
        tool_name="delete_x",
        tool_input={},
        layer=4,
    )
    store.add(pending)
    resolved = store.resolve("tuid-3", "cancel")
    assert resolved is not None
    assert resolved.decision == "cancel"
    assert store.get("tuid-3") is None


def test_confirmation_store_resolve_missing_returns_none() -> None:
    store = ConfirmationStore()
    result = store.resolve("not-there", "run")
    assert result is None


def test_confirmation_store_list_for_session() -> None:
    store = ConfirmationStore()
    for i in range(3):
        store.add(
            PendingConfirmation(
                session_id="s1",
                tool_use_id=f"tuid-{i}",
                tool_name="t",
                tool_input={},
                layer=3,
            )
        )
    store.add(
        PendingConfirmation(
            session_id="s2",
            tool_use_id="other",
            tool_name="t",
            tool_input={},
            layer=3,
        )
    )
    s1_pending = store.list_for_session("s1")
    assert len(s1_pending) == 3


def test_confirmation_store_clear_session() -> None:
    store = ConfirmationStore()
    for i in range(2):
        store.add(
            PendingConfirmation(
                session_id="sess",
                tool_use_id=f"c-{i}",
                tool_name="t",
                tool_input={},
                layer=3,
            )
        )
    store.clear_session("sess")
    assert store.list_for_session("sess") == []


# ── Surface filtering ─────────────────────────────────────────────────────────


def test_filter_by_surfaces_passes_through_untagged() -> None:
    reg = AuthorizedToolRegistry()
    t = Tool(
        name="untagged",
        description="no surface tag",
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    reg.register(t, _noop, layer=1)
    filtered = reg.filter_by_surfaces(set())
    assert "untagged" in filtered


def test_filter_by_surfaces_excludes_missing_surface() -> None:
    reg = AuthorizedToolRegistry()
    t = Tool(
        name="mktool",
        description="Marketing tool [surface:marketing-os]",
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    reg.register(t, _noop, layer=1)
    # marketing-os not in available
    filtered = reg.filter_by_surfaces({"okr"})
    assert "mktool" not in filtered


def test_filter_by_surfaces_includes_available_surface() -> None:
    reg = AuthorizedToolRegistry()
    t = Tool(
        name="okrtool",
        description="OKR tool [surface:okr]",
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    reg.register(t, _noop, layer=1)
    filtered = reg.filter_by_surfaces({"okr", "writing-rules"})
    assert "okrtool" in filtered
