"""Tests for the defensive fix bundle (briefs/defensive-fix-bundle.md).

Verifies that the three direct-SDK call sites now obtain their adapter via the
provider abstraction rather than instantiating AnthropicAdapter / AsyncAnthropic
directly.  No real LLM calls — all adapter interactions are intercepted.

Coverage:
  1. graph_extractor._default_call_model uses complete_with_fallback (not AsyncAnthropic())
  2. spawn_subagent default path uses resolve_adapter (not AnthropicAdapter())
  3. NoProviderAvailableError surfaces as RuntimeError in _default_call_model
  4. NoProviderAvailableError in spawn_subagent results in failed output
  5. No raw AsyncAnthropic() instantiation in graph_extractor
  6. No raw AnthropicAdapter() instantiation in spawn_subagent

Note: workflow_executor resolve_adapter tests live in
artemis/builders/tests/test_workflow_executor_resolve_adapter.py
(requires the db_session fixture from that module's conftest).

2026-08-13: graph_extractor._default_call_model was refactored (commit 6c011b7,
"Gemini rate-limit safety net") to route through
artemis.providers.fallback.complete_with_fallback instead of calling
resolve_adapter directly — resolve_adapter no longer exists as a module-level
name in graph_extractor.py. Tests 1/3/5 below were never updated after that
refactor and were failing with AttributeError (patching a symbol that no
longer exists); updated to patch complete_with_fallback, the real seam.
spawn_subagent (tests 2/4/6) is unaffected — it still calls resolve_adapter
directly and those patches remain valid.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_fake_completion_response(text: str) -> Any:
    """Return a minimal CompletionResponse-like object."""
    from artemis.agent.client import CompletionResponse
    from artemis.agent.types import Message, TextBlock, Usage

    return CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text=text)]),
        stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=5),
    )


# ── 1. graph_extractor._default_call_model via complete_with_fallback ─────────


async def test_graph_extractor_default_call_model_uses_complete_with_fallback() -> None:
    """_default_call_model must call complete_with_fallback, not AsyncAnthropic()."""
    from artemis.memory.graph_extractor import _default_call_model

    fake_response = _make_fake_completion_response('{"entities": [], "relations": []}')

    with patch(
        "artemis.memory.graph_extractor.complete_with_fallback",
        return_value=fake_response,
    ) as mock_cwf:
        result = await _default_call_model(
            "Jon works on Writing Studio", "claude-haiku-4-5-20251001"
        )

    mock_cwf.assert_called_once()
    assert mock_cwf.call_args.kwargs.get("primary") == "claude-code"
    assert mock_cwf.call_args.kwargs.get("fallback") == "claude-code"
    assert '{"entities": [], "relations": []}' in result


async def test_graph_extractor_default_call_model_no_raw_anthropic_import() -> None:
    """Verify no anthropic.AsyncAnthropic() is instantiated in _default_call_model."""
    # If AsyncAnthropic is constructed, this mock will capture it
    instantiation_count = 0

    class TrackingAsyncAnthropic:
        def __init__(self) -> None:
            nonlocal instantiation_count
            instantiation_count += 1

    fake_response = _make_fake_completion_response('{"entities": [], "relations": []}')

    with (
        patch(
            "artemis.memory.graph_extractor.complete_with_fallback",
            return_value=fake_response,
        ),
        patch("anthropic.AsyncAnthropic", TrackingAsyncAnthropic),
    ):
        from artemis.memory.graph_extractor import _default_call_model

        await _default_call_model("Jon leads the team", "claude-haiku-4-5-20251001")

    assert instantiation_count == 0, (
        "_default_call_model must not instantiate AsyncAnthropic() directly"
    )


async def test_graph_extractor_default_call_model_no_provider_raises_runtime_error() -> None:
    """NoProviderAvailableError from complete_with_fallback must propagate as RuntimeError."""
    from artemis.memory.graph_extractor import _default_call_model
    from artemis.providers.resolver import NoProviderAvailableError

    with (
        patch(
            "artemis.memory.graph_extractor.complete_with_fallback",
            side_effect=NoProviderAvailableError("no provider"),
        ),
        pytest.raises(RuntimeError, match="no provider available"),
    ):
        await _default_call_model("some content", "claude-haiku-4-5-20251001")


# ── 2. spawn_subagent default path via resolve_adapter ────────────────────────


async def test_spawn_subagent_uses_resolve_adapter() -> None:
    """_spawn_subagent must call resolve_adapter, not AnthropicAdapter().

    resolve_adapter is imported lazily inside the function body, so we patch
    the canonical module location: artemis.providers.resolver.resolve_adapter.
    """
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
    from artemis.floating_artemis.tools.core import _spawn_subagent

    fake_adapter = FakeAdapter([ScriptedReply(text="Sub-agent done.")])

    mock_db_session = AsyncMock()
    mock_db_session.commit = AsyncMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_db_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "artemis.providers.resolver.resolve_adapter",
            return_value=fake_adapter,
        ) as mock_resolve,
        patch(
            "artemis.builders.repository.create_agent_run", new=AsyncMock(return_value=MagicMock())
        ),
        patch(
            "artemis.builders.repository.set_agent_run_completed",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch("artemis.db.SessionLocal", return_value=mock_cm),
    ):
        result_str = await _spawn_subagent({"task": "summarize board minutes"})

    mock_resolve.assert_called_once_with(provider="claude-code")
    data = json.loads(result_str)
    assert data["ok"] is True


async def test_spawn_subagent_no_raw_anthropic_adapter_import() -> None:
    """Verify AnthropicAdapter() is not instantiated in spawn_subagent."""
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
    from artemis.floating_artemis.tools.core import _spawn_subagent

    fake_adapter = FakeAdapter([ScriptedReply(text="result")])
    instantiation_count = 0

    class TrackingAnthropicAdapter:
        def __init__(self, **kwargs: Any) -> None:
            nonlocal instantiation_count
            instantiation_count += 1

    mock_db_session = AsyncMock()
    mock_db_session.commit = AsyncMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_db_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("artemis.providers.resolver.resolve_adapter", return_value=fake_adapter),
        patch("artemis.agent.client.AnthropicAdapter", TrackingAnthropicAdapter),
        patch(
            "artemis.builders.repository.create_agent_run", new=AsyncMock(return_value=MagicMock())
        ),
        patch(
            "artemis.builders.repository.set_agent_run_completed",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch("artemis.db.SessionLocal", return_value=mock_cm),
    ):
        await _spawn_subagent({"task": "audit signal #42"})

    assert instantiation_count == 0, (
        "spawn_subagent must not instantiate AnthropicAdapter() directly"
    )


async def test_spawn_subagent_no_provider_returns_failed_output() -> None:
    """NoProviderAvailableError from resolve_adapter must result in a failed output."""
    from artemis.floating_artemis.tools.core import _spawn_subagent
    from artemis.providers.resolver import NoProviderAvailableError

    mock_db_session = AsyncMock()
    mock_db_session.commit = AsyncMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_db_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "artemis.providers.resolver.resolve_adapter",
            side_effect=NoProviderAvailableError("no provider"),
        ),
        patch(
            "artemis.builders.repository.create_agent_run", new=AsyncMock(return_value=MagicMock())
        ),
        patch(
            "artemis.builders.repository.set_agent_run_completed",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch("artemis.db.SessionLocal", return_value=mock_cm),
    ):
        result_str = await _spawn_subagent({"task": "audit something"})

    data = json.loads(result_str)
    assert data["ok"] is False
    assert "Sub-agent failed" in data["output"] or "no provider" in data["output"].lower()
