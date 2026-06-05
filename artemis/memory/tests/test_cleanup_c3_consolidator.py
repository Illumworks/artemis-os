"""C3 cleanup tests — consolidator routes through provider abstraction.

Verifies:
- LLM call goes through the adapter, not raw Anthropic SDK.
- No-provider path: ERROR log + counter bump, returns [].
- LLM exception path: ERROR log + counter bump, returns [].
- Parse failure path (regression guard): retry exhausted → counter bump, returns [].
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from artemis.agent.client import CompletionRequest, CompletionResponse
from artemis.agent.types import Message, TextBlock, Usage
from artemis.memory.consolidator import CONSOLIDATION_FAILURE_COUNTERS, consolidate_observations
from artemis.memory.schemas import Observation
from artemis.providers.resolver import NoProviderAvailableError

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_counters() -> None:
    """Reset module-level failure counters between tests."""
    CONSOLIDATION_FAILURE_COUNTERS["llm_call"] = 0
    CONSOLIDATION_FAILURE_COUNTERS["parse"] = 0
    CONSOLIDATION_FAILURE_COUNTERS["no_provider"] = 0


# ── Helpers ───────────────────────────────────────────────────────────────────


def _obs(
    obs_id: int,
    content: str,
    category: str = "discovery",
    source_quality: float = 0.7,
) -> Observation:
    return Observation(
        id=obs_id,
        scope_kind="workspace",
        scope_id="ws-c3-test",
        category=category,
        content=content,
        content_hash="abc",
        score=1.0,
        hit_count=0,
        source_quality=source_quality,
        user_confirmed=False,
        valid_from=None,
        valid_until=None,
        superseded_by=None,
        owner_user_id=None,
        created_at=datetime.now(UTC),
        accessed_at=datetime.now(UTC),
    )


def _make_response(proposals: list[dict[str, Any]], removed_ids: list[int]) -> CompletionResponse:
    payload = json.dumps({"optimized": proposals, "removed_ids": removed_ids, "summary": "test"})
    return CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text=payload)]),
        stop_reason="end_turn",
        usage=Usage(),
    )


class _FakeAdapter:
    """Fake adapter with scripted response or exception."""

    def __init__(self, response: CompletionResponse | Exception) -> None:
        self._response = response
        self.call_count = 0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.call_count += 1
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


# ── C3 tests ──────────────────────────────────────────────────────────────────


async def test_c3_routes_through_adapter() -> None:
    """Happy path: fake adapter returns valid JSON, counters stay at zero."""
    obs1 = _obs(1, "Federal grant opportunity for early literacy programs was announced.")
    obs2 = _obs(2, "New Title I supplemental funding available for rural reading programs.")

    fake_response = _make_response(
        proposals=[
            {
                "category": "discovery",
                "content": "Federal and Title I funding available for literacy programs.",
                "evidence_from_ids": [1, 2],
            }
        ],
        removed_ids=[],
    )
    fake_adapter = _FakeAdapter(fake_response)

    result = await consolidate_observations([obs1, obs2], adapter=fake_adapter)

    assert len(result) == 1
    assert result[0].content == "Federal and Title I funding available for literacy programs."
    assert 1 in result[0].evidence_from_ids
    assert 2 in result[0].evidence_from_ids
    assert CONSOLIDATION_FAILURE_COUNTERS == {"llm_call": 0, "parse": 0, "no_provider": 0}
    # Adapter was called — not the raw SDK
    assert fake_adapter.call_count == 1


async def test_c3_no_provider_path(caplog: pytest.LogCaptureFixture) -> None:
    """resolve_adapter raises NoProviderAvailableError → returns [], ERROR logged, counter bumped."""
    obs1 = _obs(1, "Federal grant opportunity for early literacy programs was announced.")
    obs2 = _obs(2, "New Title I supplemental funding available for rural reading programs.")

    with (
        caplog.at_level(logging.ERROR, logger="artemis.memory.consolidator"),
        patch(
            "artemis.memory.consolidator.resolve_adapter",
            side_effect=NoProviderAvailableError("no providers"),
        ),
    ):
        result = await consolidate_observations([obs1, obs2])

    assert result == []
    assert CONSOLIDATION_FAILURE_COUNTERS["no_provider"] == 1
    assert CONSOLIDATION_FAILURE_COUNTERS["llm_call"] == 0
    assert CONSOLIDATION_FAILURE_COUNTERS["parse"] == 0
    error_messages = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("no provider" in m.lower() or "provider" in m.lower() for m in error_messages)


async def test_c3_llm_exception_path(caplog: pytest.LogCaptureFixture) -> None:
    """Adapter.complete() raises RuntimeError → returns [], ERROR logged, llm_call counter bumped."""
    obs1 = _obs(1, "Federal grant opportunity for early literacy programs was announced.")
    obs2 = _obs(2, "New Title I supplemental funding available for rural reading programs.")

    fake_adapter = _FakeAdapter(RuntimeError("connection refused"))

    with caplog.at_level(logging.ERROR, logger="artemis.memory.consolidator"):
        result = await consolidate_observations([obs1, obs2], adapter=fake_adapter)

    assert result == []
    assert CONSOLIDATION_FAILURE_COUNTERS["llm_call"] == 1
    assert CONSOLIDATION_FAILURE_COUNTERS["no_provider"] == 0
    assert CONSOLIDATION_FAILURE_COUNTERS["parse"] == 0
    error_messages = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("LLM call failed" in m or "llm" in m.lower() for m in error_messages)


async def test_c3_parse_failure_path(caplog: pytest.LogCaptureFixture) -> None:
    """Adapter returns malformed JSON both attempts → returns [], parse counter bumped."""
    obs1 = _obs(1, "Federal grant opportunity for early literacy programs was announced.")
    obs2 = _obs(2, "New Title I supplemental funding available for rural reading programs.")

    bad_response = CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text="not valid json {{{")]),
        stop_reason="end_turn",
        usage=Usage(),
    )
    fake_adapter = _FakeAdapter(bad_response)

    with caplog.at_level(logging.ERROR, logger="artemis.memory.consolidator"):
        result = await consolidate_observations([obs1, obs2], adapter=fake_adapter)

    assert result == []
    # Two attempts were made (retry loop)
    assert fake_adapter.call_count == 2
    assert CONSOLIDATION_FAILURE_COUNTERS["parse"] == 1
    assert CONSOLIDATION_FAILURE_COUNTERS["llm_call"] == 0
    assert CONSOLIDATION_FAILURE_COUNTERS["no_provider"] == 0
    error_messages = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("retry" in m.lower() or "failed" in m.lower() for m in error_messages)
