"""Pipeline routes (PIPE1 + PIPE4 + AI Assistant panel) — /api/pipelines*.

Endpoints:
  GET    /api/pipelines         — list (latest_run embedded, single LATERAL query)
  GET    /api/pipelines/        — no-slash compat alias
  POST   /api/pipelines/        — create
  GET    /api/pipelines/{id}/export — portable JSON bundle
  POST   /api/pipelines/import  — import portable JSON bundle
  GET    /api/pipelines/{id}    — detail with latest run
  PATCH  /api/pipelines/{id}    — update (full nodes/edges replace)
  DELETE /api/pipelines/{id}    — soft delete (status → archived)
  DELETE /api/pipelines/{id}/permanent — hard delete archived pipeline
  POST   /api/pipelines/{id}/enable  — flip status to active
  POST   /api/pipelines/{id}/disable — flip status to paused
  POST   /api/pipelines/{id}/run     — manual trigger (PIPE4 executes immediately)
  GET    /api/pipelines/{id}/runs    — cursor-paginated run history
  POST   /api/pipeline-runs/{run_id}/cancel  — mark cancelled
  POST   /api/pipeline-runs/{run_id}/resume  — resume after approval (PIPE4)

  Slack approval callback (PIPE4):
  POST   /api/slack/pipeline-approval-callback — Slack interactive component webhook

  AI Assistant panel (canvas inline AI):
  POST   /api/pipelines/{id}/assistant/turn  — SSE stream; structured proposals
  GET    /api/pipelines/{id}/assistant/conversation — conversation history
  DELETE /api/pipelines/{id}/assistant/conversation — clear history
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found, validation_failed
from artemis.pipelines import repository as repo
from artemis.pipelines.schemas import (
    PipelineCreate,
    PipelineExportBundle,
    PipelineImportResult,
    PipelineRunRequest,
    PipelineUpdate,
    pipeline_run_to_schema,
    pipeline_to_schema,
)


def _resolve_adapter() -> Any:
    """Walk provider cascade; raises HTTP 503 if nothing available."""
    from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter

    try:
        return resolve_adapter()
    except NoProviderAvailableError as exc:
        raise bad_request(
            "No LLM provider is available. Add an API key in Integrations.",
            "no_provider",
        ) from exc


router = APIRouter(
    tags=["pipelines"],
    dependencies=[Depends(require_token)],
)

logger = logging.getLogger(__name__)

# ── Dispatch registry ─────────────────────────────────────────────────────────
# #103: pipeline runs execute OUT-OF-PROCESS via ``python -m
# artemis.pipelines.run_cli <run_id>`` so a crashing agent_invocation node
# (claude grandchild, semaphore leak, etc.) can never take down the
# FastAPI web app — the same fix #102 applied to scheduled scouts.
#
# A background reaper task awaits the subprocess so zombies don't pile up
# and the returncode is logged. asyncio holds only a WEAK reference to
# fire-and-forget tasks, so this set keeps a STRONG reference for the
# lifetime of each reaper; the done-callback removes the entry so the set
# doesn't leak. Pipeline runs can run for many minutes, so there is no
# wall-clock kill — the run owns its own lifecycle (gate timeouts already
# handled by the scheduler).
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()

# Repo root — passed as cwd so the subprocess inherits the same project
# layout regardless of where uvicorn was launched from.
_REPO_ROOT = Path(__file__).resolve().parents[2]


async def _reap_run_subprocess(run_id: str, proc: asyncio.subprocess.Process) -> None:
    """Await a dispatched run subprocess, log its returncode + tail."""
    try:
        stdout, _ = await proc.communicate()
    except Exception:
        logger.exception("pipeline run %s: subprocess communicate() failed", run_id)
        return

    tail = (stdout or b"").decode(errors="replace").strip().splitlines()[-1:] if stdout else []
    last_line = tail[0] if tail else "(no output)"
    if proc.returncode == 0:
        logger.info("pipeline run %s: subprocess exit=0 last_line=%s", run_id, last_line)
    else:
        logger.warning(
            "pipeline run %s: subprocess exit=%s last_line=%s",
            run_id,
            proc.returncode,
            last_line,
        )


def _dispatch_execution(run_id: str) -> None:
    """Spawn the pipeline run as an isolated subprocess and reap it in the background.

    The pipeline_runs row already exists in the DB before dispatch, so the
    subprocess only needs the run_id. All state flows through the DB.
    """
    # Lazy import so a missing module path during tests doesn't break import time.
    from artemis.pipelines import run_cli

    argv = [sys.executable, "-m", run_cli.MODULE_NAME, run_id]

    async def _spawn_and_reap() -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(_REPO_ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError:
            logger.exception("pipeline run %s: failed to spawn subprocess argv=%r", run_id, argv)
            return
        await _reap_run_subprocess(run_id, proc)

    task: asyncio.Task[None] = asyncio.create_task(_spawn_and_reap())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run_to_dict(run: Any) -> dict[str, Any]:
    return pipeline_run_to_schema(run).model_dump(by_alias=True)


def _pipeline_to_dict(p: Any, latest_run: Any | None = None) -> dict[str, Any]:
    return pipeline_to_schema(p, latest_run).model_dump(by_alias=True)


def _resolve_upstream_agent_slug(
    gate_node_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> str | None:
    """Walk the pipeline graph backwards from gate_node_id to find the nearest
    upstream agent_invocation node and return its configured agent_id.

    Returns None if no upstream agent_invocation node can be found (non-fatal).
    This enables MC4 to attach an agent:<slug> scope to the gate observation so
    the agent can query its own past rejections (C-3 read path).
    """
    # Build reverse adjacency: target_node_id → list[source_node_id]
    reverse: dict[str, list[str]] = {}
    for edge in edges:
        src = edge.get("source_node_id", "")
        tgt = edge.get("target_node_id", "")
        if src and tgt:
            reverse.setdefault(tgt, []).append(src)

    # Build node lookup: node_id → node dict
    node_by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        nid = node.get("id", "")
        if nid:
            node_by_id[nid] = node

    # BFS backwards from the gate node
    visited: set[str] = set()
    queue: list[str] = list(reverse.get(gate_node_id, []))
    while queue:
        nid = queue.pop(0)
        if nid in visited:
            continue
        visited.add(nid)
        candidate_node: dict[str, Any] | None = node_by_id.get(nid)
        if candidate_node is not None and candidate_node.get("type") == "agent_invocation":
            config: dict[str, Any] = candidate_node.get("config") or {}
            raw_agent_id = config.get("agent_id", "")
            agent_id: str = str(raw_agent_id) if raw_agent_id else ""
            if agent_id:
                return agent_id
        # Continue searching upstream
        queue.extend(reverse.get(nid, []))
    return None


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/api/pipelines")
@router.get("/api/pipelines/")
async def list_pipelines(
    status: str | None = Query(default=None),
    owner: int | None = Query(default=None),
    has_trigger: bool | None = Query(default=None, alias="hasTrigger"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """List pipelines with latest_run embedded (single LATERAL JOIN query)."""
    rows = await repo.list_pipelines(
        session,
        status=status,
        owner_user_id=owner,
        has_trigger=has_trigger,
        limit=limit,
        cursor=cursor,
    )
    return [_pipeline_to_dict(p, run) for p, run in rows]


# ── Create ────────────────────────────────────────────────────────────────────


@router.post("/api/pipelines/", status_code=201)
async def create_pipeline(
    body: PipelineCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Create a new pipeline definition."""
    p = await repo.create_pipeline(
        session,
        name=body.name,
        description=body.description,
        nodes=body.nodes,
        edges=body.edges,
        trigger_config=body.trigger_config,
        status=body.status,
        owner_user_id=body.owner_user_id,
        metadata_=body.metadata,
    )
    await session.commit()
    return _pipeline_to_dict(p)


# ── Detail ────────────────────────────────────────────────────────────────────


@router.get("/api/pipelines/{pipeline_id}/export")
async def export_pipeline(
    pipeline_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return a portable, credential-free pipeline JSON bundle."""
    try:
        bundle = await repo.build_export_bundle(
            session,
            pipeline_id,
            exported_from=str(request.base_url).rstrip("/"),
        )
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    return bundle.model_dump(mode="json")


@router.post("/api/pipelines/import", status_code=201)
async def import_pipeline(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Import a portable pipeline bundle, creating missing agents without overwriting."""
    if body.get("format_version") != "1":
        raise validation_failed(
            {"format_version": "Format upgrade required: only format_version '1' is supported"}
        )
    try:
        bundle = PipelineExportBundle.model_validate(body)
        result = await repo.import_bundle(session, bundle)
    except ValidationError as exc:
        raise validation_failed({"bundle": str(exc)}) from exc
    except KeyError as exc:
        raise bad_request(f"Missing pipeline field: {exc}", "invalid_pipeline_import")  # noqa: B904
    except ValueError as exc:
        raise bad_request(str(exc), "invalid_pipeline_import")  # noqa: B904
    await session.commit()
    return PipelineImportResult(**result).model_dump()


@router.get("/api/pipelines/{pipeline_id}")
async def get_pipeline(
    pipeline_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return a single pipeline with its latest run embedded."""
    try:
        p, run = await repo.get_pipeline_with_latest_run(session, pipeline_id)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    return _pipeline_to_dict(p, run)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/api/pipelines/{pipeline_id}")
async def update_pipeline(
    pipeline_id: str,
    body: PipelineUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Update mutable fields of a pipeline. Nodes/edges are fully replaced when supplied."""
    updates: dict[str, Any] = {}
    for field in [
        "name",
        "description",
        "nodes",
        "edges",
        "trigger_config",
        "status",
        "owner_user_id",
        "metadata",
    ]:
        val = getattr(body, field)
        if val is not None:
            updates[field] = val
    try:
        p = await repo.update_pipeline(session, pipeline_id, **updates)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    await session.commit()
    return _pipeline_to_dict(p)


# ── Soft delete ───────────────────────────────────────────────────────────────


@router.delete("/api/pipelines/{pipeline_id}", status_code=204)
async def delete_pipeline(
    pipeline_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Soft delete: set status=archived. Row never removed from DB."""
    try:
        await repo.archive_pipeline(session, pipeline_id)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    await session.commit()


@router.delete("/api/pipelines/{pipeline_id}/permanent", status_code=204)
async def permanently_delete_pipeline(
    pipeline_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Hard delete an already archived pipeline and its run history."""
    try:
        await repo.permanently_delete_pipeline(session, pipeline_id)
    except RuntimeError as exc:
        raise conflict(str(exc), "pipeline_must_be_archived")  # noqa: B904
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    await session.commit()


# ── Enable / Disable ──────────────────────────────────────────────────────────


@router.post("/api/pipelines/{pipeline_id}/enable")
async def enable_pipeline(
    pipeline_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Flip pipeline status to active."""
    try:
        p = await repo.update_pipeline(session, pipeline_id, status="active")
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    await session.commit()
    return _pipeline_to_dict(p)


@router.post("/api/pipelines/{pipeline_id}/disable")
async def disable_pipeline(
    pipeline_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Flip pipeline status to paused."""
    try:
        p = await repo.update_pipeline(session, pipeline_id, status="paused")
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    await session.commit()
    return _pipeline_to_dict(p)


# ── Manual trigger ────────────────────────────────────────────────────────────


@router.post("/api/pipelines/{pipeline_id}/run", status_code=202)
async def run_pipeline(
    pipeline_id: str,
    body: PipelineRunRequest | None = None,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Manually trigger a pipeline run.

    Creates a pipeline_runs row and immediately dispatches the PIPE4 executor
    via an asyncio background task. The response returns the run row in
    'running' status; the caller can poll GET /runs for progress.
    """
    body = body or PipelineRunRequest()
    try:
        p = await repo.get_pipeline(session, pipeline_id)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904

    if p.status == "archived":
        raise bad_request("Cannot run an archived pipeline", "pipeline_archived")

    existing = await repo.acquire_run_lock(session, pipeline_id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "pipeline_run_in_flight",
                "in_flight_run_id": existing.id,
                "message": (
                    f"Pipeline already has an in-flight run (id={existing.id}, "
                    f"status={existing.status}). "
                    "Cancel or wait for it to complete before starting a new run."
                ),
            },
        )

    run = await repo.create_pipeline_run(
        session,
        pipeline_id=pipeline_id,
        status="queued",
        trigger="manual",
        triggered_by=body.triggered_by or "manual",
        metadata_=body.metadata,
    )
    await session.commit()
    run_id = run.id

    # Dispatch execution in background so the HTTP response returns immediately
    _dispatch_execution(run_id)

    return _run_to_dict(run)


# ── Run history ───────────────────────────────────────────────────────────────


@router.get("/api/pipeline-runs")
async def list_all_runs(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """Return recent pipeline runs across all pipelines (for run history page)."""
    runs = await repo.list_all_pipeline_runs(session, status=status, limit=limit, cursor=cursor)
    return [_run_to_dict(r) for r in runs]


@router.get("/api/pipelines/{pipeline_id}/runs")
async def list_runs(
    pipeline_id: str,
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """Return run history for a pipeline (cursor-paginated, newest first)."""
    try:
        await repo.get_pipeline(session, pipeline_id)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    runs = await repo.list_pipeline_runs(session, pipeline_id, limit=limit, cursor=cursor)
    return [_run_to_dict(r) for r in runs]


# ── Cancel ────────────────────────────────────────────────────────────────────


@router.post("/api/pipeline-runs/{run_id}/cancel", status_code=200)
async def cancel_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Cancel an in-flight or queued pipeline run."""
    try:
        run = await repo.get_pipeline_run(session, run_id)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_run_not_found")  # noqa: B904

    if run.status in ("succeeded", "failed", "cancelled"):
        raise bad_request(
            f"Cannot cancel a run with status '{run.status}'",
            "pipeline_run_already_terminal",
        )

    run = await repo.update_pipeline_run(
        session,
        run_id,
        status="cancelled",
        completed_at=datetime.now(UTC),
    )
    await session.commit()
    return _run_to_dict(run)


# ── PIPE4: Execute pipeline run (background task) ─────────────────────────────


async def _execute_pipeline_run(run_id: str) -> None:
    """Background task: run the pipeline executor in its own DB session."""
    from artemis.db import SessionLocal
    from artemis.pipelines.executor import PipelineExecutor
    from artemis.pipelines.models import PipelineRun

    async with SessionLocal() as session:
        try:
            executor = PipelineExecutor(run_id)
            await executor.run(session)
            await session.commit()
        except Exception as exc:
            logger.exception("Pipeline run %s failed before executor startup", run_id)
            await session.rollback()
            await session.execute(
                update(PipelineRun)
                .where(PipelineRun.id == run_id)
                .where(PipelineRun.status == "queued")
                .values(
                    status="failed",
                    error_message=f"Executor crashed before start: {exc}",
                    completed_at=datetime.now(UTC),
                )
            )
            await session.commit()
            raise


# ── PIPE4: Resume ─────────────────────────────────────────────────────────────


class ResumeRunRequest(BaseModel):
    """Body for POST /api/pipeline-runs/{run_id}/resume."""

    node_id: str
    decision: str  # "approved" | "rejected"
    actor: str  # email of the human who decided
    reason: str | None = None  # optional reject reason (C1.1: never required)


async def _prepare_pipeline_resume(
    session: AsyncSession,
    run_id: str,
    *,
    node_id: str,
    decision: str,
    actor: str,
    reason: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Validate + stage a gate resume without committing.

    Shared by the PIPE4 HTTP resume route and the marketing initiation confirm
    route so both seams honor the same gate-release contract.
    When reason is provided (optional), it is stored in gate_state["reason"]
    so downstream memory-carryover calls can attach it to the observation.
    """
    try:
        run = await repo.get_pipeline_run(session, run_id)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_run_not_found") from exc

    if run.status not in ("awaiting_approval", "running"):
        raise bad_request(
            f"Run '{run_id}' is not awaiting approval (status={run.status})",
            "pipeline_run_not_awaiting_approval",
        )

    if decision not in ("approved", "rejected"):
        raise bad_request(
            f"decision must be 'approved' or 'rejected', got {decision!r}",
            "invalid_decision",
        )

    node_states: dict[str, Any] = dict(run.node_states or {})
    gate_state = node_states.get(node_id)
    if not gate_state or not isinstance(gate_state, dict):
        raise bad_request(
            f"node_id '{node_id}' not found in node_states for run '{run_id}'",
            "gate_node_not_found",
        )

    if gate_state.get("status") not in ("suspended", "running"):
        raise bad_request(
            f"Gate '{node_id}' is not suspended (status={gate_state.get('status')})",
            "gate_not_suspended",
        )

    gate_state["decision"] = decision
    gate_state["decided_at"] = datetime.now(UTC).isoformat()
    gate_state["decided_by"] = actor
    if reason:
        gate_state["reason"] = reason
    node_states[node_id] = gate_state
    run.node_states = node_states
    flag_modified(run, "node_states")
    run.status = "running"
    await session.flush()
    return run, gate_state


@router.post("/api/pipeline-runs/{run_id}/resume", status_code=200)
async def resume_run(
    run_id: str,
    body: ResumeRunRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Resume a pipeline run after an approval gate is resolved.

    1. Validates run is awaiting_approval and node_id matches a suspended gate.
    2. Updates node_states[node_id] with decision, actor, decided_at.
    3. Cancels the scheduled timeout job for this gate.
    4. Re-dispatches the executor in background to continue from next node.
    """
    from artemis.marketing.routes.approvals import (
        apply_approval_decision,
        find_pending_pipe4_approval,
    )

    subject_id = f"{run_id}:{body.node_id}"
    approval = await find_pending_pipe4_approval(session, subject_id=subject_id)
    if approval is not None:
        await apply_approval_decision(
            session,
            approval=approval,
            decision=body.decision,
            decided_by=body.actor or "operator",
            decision_payload={
                **(
                    dict(approval.decision_payload)
                    if isinstance(approval.decision_payload, dict)
                    else {}
                ),
                "decision": body.decision,
                "decided_by": body.actor or "operator",
                "decided_at": datetime.now(UTC).isoformat(),
                **({"reason": body.reason} if body.reason else {}),
            },
        )
    else:
        run, _ = await _prepare_pipeline_resume(
            session,
            run_id,
            node_id=body.node_id,
            decision=body.decision,
            actor=body.actor,
            reason=body.reason,
        )
        await session.commit()

        try:
            import contextlib

            from artemis.pipelines.scheduler import get_pipeline_scheduler

            scheduler = get_pipeline_scheduler()
            if scheduler.running:
                job_id = f"gate_timeout_{run_id}_{body.node_id}"
                with contextlib.suppress(Exception):
                    scheduler.remove_job(job_id)
        except Exception:
            logger.warning("Could not cancel timeout job for run %s gate %s", run_id, body.node_id)

        _dispatch_execution(run_id)

        _agent_slug_mc4: str | None = None
        try:
            _pipeline = await repo.get_pipeline(session, run.pipeline_id)
            _agent_slug_mc4 = _resolve_upstream_agent_slug(
                body.node_id, _pipeline.nodes or [], _pipeline.edges or []
            )
        except Exception:
            pass

        from artemis.config import settings as _settings

        if _settings.env != "test":
            import asyncio as _asyncio

            from artemis.builder.memory_carryover import write_pipeline_gate_decision_observation

            _asyncio.create_task(
                write_pipeline_gate_decision_observation(
                    pipeline_run_id=run_id,
                    pipeline_id=run.pipeline_id,
                    node_id=body.node_id,
                    decision=body.decision,
                    decided_by=body.actor or "operator",
                    decision_payload={"pipeline_name": run.pipeline_id},
                    rejection_reason=body.reason,
                    agent_slug=_agent_slug_mc4,
                )
            )

    run = await repo.get_pipeline_run(session, run_id)
    return _run_to_dict(run)


# ── PIPE4: Slack approval callback ────────────────────────────────────────────

_slack_callback_router = APIRouter(tags=["pipelines"])


@_slack_callback_router.post("/api/slack/pipeline-approval-callback", status_code=200)
async def slack_pipeline_approval_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Handle Slack interactive component callback for pipeline gate approvals.

    Slack sends a URL-encoded 'payload' field containing JSON with action info.
    Button value format: '{run_id}:{node_id}:{decision}'

    This endpoint is unauthenticated (Slack doesn't send our auth token);
    validate via the action_id prefix to ensure it's a pipeline approval action.
    """
    import json

    form = await request.form()
    payload_raw = form.get("payload", "")
    if not payload_raw:
        return {"ok": True, "message": "No payload"}

    try:
        payload = json.loads(str(payload_raw))
    except Exception:
        logger.warning("Invalid Slack callback payload (not JSON)")
        return {"ok": True}

    actions = payload.get("actions", [])
    if not actions:
        return {"ok": True}

    action = actions[0]
    action_id: str = action.get("action_id", "")
    if not action_id.startswith("pipeline_approval"):
        return {"ok": True}

    if action_id == "pipeline_approval_view":
        return {"ok": True}

    value: str = action.get("value", "")
    parts = value.split(":", 2)
    if len(parts) != 3:
        logger.warning("Invalid pipeline approval button value: %r", value)
        return {"ok": True}

    run_id, node_id, decision = parts
    actor_email = ""
    try:
        user_obj = payload.get("user", {})
        actor_email = str(user_obj.get("name") or user_obj.get("id") or "slack_user")
    except Exception:
        actor_email = "slack_user"

    if decision not in ("approved", "rejected"):
        return {"ok": True}

    # Stage the gate release through the shared helper so the Slack seam honors
    # the same contract as the HTTP resume route — crucially including
    # ``flag_modified(run, "node_states")``. Without it, the shallow-copy +
    # in-place nested mutation this endpoint used to do never marked the JSONB
    # column dirty (the mutated gate dict is shared with the loaded value, so
    # SQLAlchemy detected no change), the decision never persisted, and the run
    # silently re-suspended on resume.
    from artemis.marketing.routes.approvals import (
        apply_approval_decision,
        find_pending_pipe4_approval,
    )

    try:
        approval = await find_pending_pipe4_approval(session, subject_id=f"{run_id}:{node_id}")
        if approval is not None:
            await apply_approval_decision(
                session,
                approval=approval,
                decision=decision,
                decided_by=actor_email or "slack_user",
                decision_payload={
                    **(
                        dict(approval.decision_payload)
                        if isinstance(approval.decision_payload, dict)
                        else {}
                    ),
                    "decision": decision,
                    "decided_by": actor_email or "slack_user",
                    "decided_at": datetime.now(UTC).isoformat(),
                    "source": "slack_callback",
                },
            )
        else:
            await _prepare_pipeline_resume(
                session,
                run_id,
                node_id=node_id,
                decision=decision,
                actor=actor_email or "slack_user",
            )
            await session.commit()
    except HTTPException:
        # Unknown run, gate not suspended, or invalid decision: ack silently.
        # Slack interaction endpoints must always 200 or the button errors out.
        return {"ok": True}

    if approval is None:
        _dispatch_execution(run_id)

    return {"ok": True}


# Register the Slack callback router (no auth requirement)
router.include_router(_slack_callback_router)


# ── AI Assistant panel ────────────────────────────────────────────────────────


class AssistantTurnRequest(BaseModel):
    """Body for POST /api/pipelines/{id}/assistant/turn."""

    message: str
    is_first_turn: bool = False


@router.post("/api/pipelines/{pipeline_id}/assistant/turn")
async def assistant_turn(
    pipeline_id: str,
    body: AssistantTurnRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> StreamingResponse:
    """SSE stream for one Pipeline AI Assistant turn.

    Stream events (same shape as O1 Builder SSE):
      turn_start, self_improvement*, assistant_token*, proposal_parsed*,
      heartbeat, turn_complete | error

    Reuses the O1 provider cascade + SSE heartbeat pattern from
    artemis/builder/routes.py::send_message_stream.
    """
    from artemis.pipelines.assistant.turn_handler import (
        AssistantPanelEvent,
        handle_assistant_turn_stream,
    )

    # Verify pipeline exists
    try:
        pipeline = await repo.get_pipeline(session, pipeline_id)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904

    # Load conversation history
    conv_row = await repo.get_or_create_ai_conversation(session, pipeline_id)
    conversation = list(conv_row.conversation or [])

    # Load recent runs for self-improvement context
    recent_runs_objs = await repo.list_pipeline_runs(session, pipeline_id, limit=5)
    recent_runs = [
        {
            "id": r.id,
            "status": r.status,
            "error_message": r.error_message,
            "node_states": r.node_states,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in recent_runs_objs
    ]

    pipeline_data = {
        "id": pipeline.id,
        "name": pipeline.name,
        "nodes": pipeline.nodes or [],
        "edges": pipeline.edges or [],
    }

    adapter = _resolve_adapter()

    user_text = body.message
    is_first_turn = body.is_first_turn

    async def _event_stream() -> AsyncIterator[str]:
        q: asyncio.Queue[AssistantPanelEvent | None] = asyncio.Queue()
        assistant_text_parts: list[str] = []

        async def _drain() -> None:
            try:
                async for ev in handle_assistant_turn_stream(
                    pipeline_id=pipeline_id,
                    user_text=user_text,
                    pipeline_data=pipeline_data,
                    conversation=conversation,
                    recent_runs=recent_runs,
                    adapter=adapter,
                    is_first_turn=is_first_turn,
                ):
                    if ev.type == "assistant_token":
                        assistant_text_parts.append(ev.payload.get("delta", ""))
                    await q.put(ev)
            finally:
                await q.put(None)

        task = asyncio.ensure_future(_drain())
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except TimeoutError:
                    import datetime as _dt

                    yield AssistantPanelEvent(
                        type="heartbeat",
                        payload={"ts": _dt.datetime.now(_dt.UTC).isoformat()},
                    ).to_sse()
                    continue
                if ev is None:
                    break
                yield ev.to_sse()
                if ev.type == "turn_complete":
                    # Persist conversation — open a fresh session context
                    try:
                        full_text = "".join(assistant_text_parts)
                        from artemis.db import SessionLocal

                        async with SessionLocal() as persist_session:
                            await repo.append_ai_message(
                                persist_session, pipeline_id, "user", user_text
                            )
                            await repo.append_ai_message(
                                persist_session, pipeline_id, "assistant", full_text
                            )
                            await persist_session.commit()
                    except Exception:
                        logger.exception(
                            "Failed to persist AI conversation for pipeline %s", pipeline_id
                        )
                    break
                if ev.type == "error":
                    break
        except asyncio.CancelledError:
            logger.info(
                "pipeline assistant SSE cancelled for pipeline %s (client disconnect)",
                pipeline_id,
            )
            task.cancel()
        finally:
            if not task.done():
                task.cancel()

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers=headers,
    )


@router.get("/api/pipelines/{pipeline_id}/assistant/conversation")
async def get_ai_conversation(
    pipeline_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return the current AI conversation history for a pipeline."""
    try:
        await repo.get_pipeline(session, pipeline_id)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    conv_row = await repo.get_or_create_ai_conversation(session, pipeline_id)
    await session.commit()
    return {"pipeline_id": pipeline_id, "conversation": conv_row.conversation or []}


@router.delete("/api/pipelines/{pipeline_id}/assistant/conversation", status_code=204)
async def clear_ai_conversation(
    pipeline_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Clear the AI conversation history for a pipeline."""
    try:
        await repo.get_pipeline(session, pipeline_id)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    await repo.clear_ai_conversation(session, pipeline_id)
    await session.commit()
