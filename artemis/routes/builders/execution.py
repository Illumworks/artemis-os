"""Execution routes for Builders — /api/builders/... run endpoints.

Phase F2b: POST endpoints that trigger synchronous agent/workflow/chain/DAG runs.
Streaming (E2) is out of scope; these block until the run completes.

Endpoints:
  POST /api/agents/{agent_id}/run
  POST /api/workflows/{workflow_id}/run
  POST /api/agent-chains/{chain_id}/run
  POST /api/agent-dags/{dag_id}/run
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders.chain_executor import run_chain
from artemis.builders.dag_executor import run_dag_with_context
from artemis.builders.executor import run_agent
from artemis.builders.schemas import AgentRunRead, WorkflowRunRead
from artemis.builders.workflow_executor import run_workflow
from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import internal, not_found

router = APIRouter(
    tags=["builders-execution"],
    dependencies=[Depends(require_token)],
)


# ─────────────────────────────────────────────────────────────────────────────
# Request bodies
# ─────────────────────────────────────────────────────────────────────────────


class AgentRunRequest(BaseModel):
    user_message: str | None = Field(default=None, alias="userMessage")
    shared_context: dict[str, Any] | None = Field(default=None, alias="sharedContext")

    model_config = {"populate_by_name": True}


class WorkflowRunRequest(BaseModel):
    initial_message: str | None = Field(default=None, alias="initialMessage")

    model_config = {"populate_by_name": True}


class ChainRunRequest(BaseModel):
    initial_message: str | None = Field(default=None, alias="initialMessage")

    model_config = {"populate_by_name": True}


class DagRunRequest(BaseModel):
    initial_inputs: dict[str, str] | None = Field(default=None, alias="initialInputs")

    model_config = {"populate_by_name": True}


# ─────────────────────────────────────────────────────────────────────────────
# Agent run
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/api/agents/{agent_id}/run", status_code=200)
async def run_agent_endpoint(
    agent_id: str,
    body: AgentRunRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Run an agent synchronously. Returns the completed AgentRun row."""
    try:
        run = await run_agent(
            session=session,
            agent_id=agent_id,
            user_message=body.user_message,
            shared_context=body.shared_context,
        )
        await session.commit()
        return AgentRunRead.model_validate(run).model_dump(by_alias=True)
    except ValueError as exc:
        raise not_found(str(exc), "agent_not_found")  # noqa: B904
    except Exception as exc:
        raise internal(f"Agent run failed: {exc}")  # noqa: B904


# ─────────────────────────────────────────────────────────────────────────────
# Workflow run
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/api/workflows/{workflow_id}/run", status_code=200)
async def run_workflow_endpoint(
    workflow_id: str,
    body: WorkflowRunRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Run a workflow synchronously. Returns the completed WorkflowRun row."""
    try:
        run = await run_workflow(
            session=session,
            workflow_id=workflow_id,
            initial_message=body.initial_message,
        )
        await session.commit()
        return WorkflowRunRead.model_validate(run).model_dump(by_alias=True)
    except ValueError as exc:
        raise not_found(str(exc), "workflow_not_found")  # noqa: B904
    except Exception as exc:
        raise internal(f"Workflow run failed: {exc}")  # noqa: B904


# ─────────────────────────────────────────────────────────────────────────────
# Chain run
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/api/agent-chains/{chain_id}/run", status_code=200)
async def run_chain_endpoint(
    chain_id: str,
    body: ChainRunRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Run a chain synchronously. Returns the list of AgentRun rows."""
    try:
        runs = await run_chain(
            session=session,
            chain_id=chain_id,
            initial_message=body.initial_message,
        )
        await session.commit()
        return {"runs": [AgentRunRead.model_validate(r).model_dump(by_alias=True) for r in runs]}
    except ValueError as exc:
        raise not_found(str(exc), "chain_not_found")  # noqa: B904
    except Exception as exc:
        raise internal(f"Chain run failed: {exc}")  # noqa: B904


# ─────────────────────────────────────────────────────────────────────────────
# DAG run
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/api/agent-dags/{dag_id}/run", status_code=200)
async def run_dag_endpoint(
    dag_id: str,
    body: DagRunRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Run a DAG synchronously. Returns dict of node_id → AgentRun."""
    try:
        results = await run_dag_with_context(
            session=session,
            dag_id=dag_id,
            initial_inputs=body.initial_inputs,
        )
        await session.commit()
        return {
            "results": {
                nid: AgentRunRead.model_validate(run).model_dump(by_alias=True)
                for nid, run in results.items()
            }
        }
    except ValueError as exc:
        raise not_found(str(exc), "dag_not_found")  # noqa: B904
    except Exception as exc:
        raise internal(f"DAG run failed: {exc}")  # noqa: B904
