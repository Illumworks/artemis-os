"""Tests for workflow_executor resolve_adapter refactor (defensive-fix-bundle).

Verifies that run_workflow's default path goes through resolve_adapter rather
than instantiating AnthropicAdapter() directly.  No real LLM calls.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builders import repository as repo
from artemis.builders.workflow_executor import run_workflow

pytestmark = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_workflow_executor_default_path_uses_resolve_adapter(
    db_session: AsyncSession,
) -> None:
    """run_workflow with no model_adapter must call resolve_adapter, not AnthropicAdapter()."""
    fake_adapter = FakeAdapter([ScriptedReply(text="done")])

    async with db_session.begin():
        await repo.create_workflow(
            db_session,
            workflow_id="wf-resolve-test",
            name="Resolve Adapter WF",
            steps=[{"name": "step1", "prompt": "Do the thing"}],
        )

    with patch(
        "artemis.builders.workflow_executor.resolve_adapter", return_value=fake_adapter
    ) as mock_resolve:
        run = await run_workflow(
            session=db_session,
            workflow_id="wf-resolve-test",
            # No model_adapter passed — must use resolve_adapter
        )

    await db_session.commit()
    mock_resolve.assert_called_once_with(provider="claude-code")
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_workflow_executor_no_raw_anthropic_adapter_import(
    db_session: AsyncSession,
) -> None:
    """Verify AnthropicAdapter() is not instantiated when model_adapter is not passed."""
    fake_adapter = FakeAdapter([ScriptedReply(text="done")])
    instantiation_count = 0

    class TrackingAnthropicAdapter:
        def __init__(self, **kwargs: Any) -> None:
            nonlocal instantiation_count
            instantiation_count += 1

    async with db_session.begin():
        await repo.create_workflow(
            db_session,
            workflow_id="wf-no-raw-sdk",
            name="No Raw SDK WF",
            steps=[{"name": "step1", "prompt": "Do the thing"}],
        )

    with (
        patch("artemis.builders.workflow_executor.resolve_adapter", return_value=fake_adapter),
        patch("artemis.agent.client.AnthropicAdapter", TrackingAnthropicAdapter),
    ):
        await run_workflow(
            session=db_session,
            workflow_id="wf-no-raw-sdk",
        )

    await db_session.commit()
    assert instantiation_count == 0, (
        "workflow_executor must not instantiate AnthropicAdapter() directly"
    )


@pytest.mark.asyncio
async def test_workflow_executor_no_provider_raises_runtime_error(
    db_session: AsyncSession,
) -> None:
    """NoProviderAvailableError from resolve_adapter propagates as RuntimeError."""
    from artemis.providers.resolver import NoProviderAvailableError

    async with db_session.begin():
        await repo.create_workflow(
            db_session,
            workflow_id="wf-no-provider",
            name="No Provider WF",
            steps=[{"name": "step1", "prompt": "Do the thing"}],
        )

    with (
        patch(
            "artemis.builders.workflow_executor.resolve_adapter",
            side_effect=NoProviderAvailableError("no provider"),
        ),
        pytest.raises(RuntimeError, match="no provider available"),
    ):
        await run_workflow(
            session=db_session,
            workflow_id="wf-no-provider",
        )
