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
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run_to_dict(run: Any) -> dict[str, Any]:
    return pipeline_run_to_schema(run).model_dump(by_alias=True)


def _pipeline_to_dict(p: Any, latest_run: Any | None = None) -> dict[str, Any]:
    return pipeline_to_schema(p, latest_run).model_dump(by_alias=True)


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
    asyncio.create_task(_execute_pipeline_run(run_id))

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
    from datetime import UTC, datetime  # noqa: PLC0415

    try:
        run = await repo.get_pipeline_run(session, run_id)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_run_not_found")  # noqa: B904

    if run.status not in ("awaiting_approval", "running"):
        raise bad_request(
            f"Run '{run_id}' is not awaiting approval (status={run.status})",
            "pipeline_run_not_awaiting_approval",
        )

    if body.decision not in ("approved", "rejected"):
        raise bad_request(
            f"decision must be 'approved' or 'rejected', got {body.decision!r}",
            "invalid_decision",
        )

    node_states: dict[str, Any] = dict(run.node_states or {})
    gate_state = node_states.get(body.node_id)
    if not gate_state or not isinstance(gate_state, dict):
        raise bad_request(
            f"node_id '{body.node_id}' not found in node_states for run '{run_id}'",
            "gate_node_not_found",
        )

    if gate_state.get("status") not in ("suspended", "running"):
        raise bad_request(
            f"Gate '{body.node_id}' is not suspended (status={gate_state.get('status')})",
            "gate_not_suspended",
        )

    # Update gate state with human decision
    gate_state["decision"] = body.decision
    gate_state["decided_at"] = datetime.now(UTC).isoformat()
    gate_state["decided_by"] = body.actor
    node_states[body.node_id] = gate_state
    run.node_states = node_states
    run.status = "running"
    await session.commit()

    # Cancel the scheduled timeout job
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

    # Re-dispatch executor in background
    asyncio.create_task(_execute_pipeline_run(run_id))

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
    from datetime import UTC, datetime

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

    try:
        run = await repo.get_pipeline_run(session, run_id)
    except ValueError:
        return {"ok": True}

    if run.status not in ("awaiting_approval", "running"):
        return {"ok": True}

    node_states: dict[str, Any] = dict(run.node_states or {})
    gate_state = node_states.get(node_id, {})
    if not isinstance(gate_state, dict) or gate_state.get("status") not in ("suspended", "running"):
        return {"ok": True}

    gate_state["decision"] = decision
    gate_state["decided_at"] = datetime.now(UTC).isoformat()
    gate_state["decided_by"] = actor_email
    node_states[node_id] = gate_state
    run.node_states = node_states
    run.status = "running"
    await session.commit()

    asyncio.create_task(_execute_pipeline_run(run_id))

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
