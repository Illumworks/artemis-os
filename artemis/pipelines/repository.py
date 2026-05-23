"""Async repository helpers for the Pipelines domain (PIPE1).

Conventions:
- Raise ValueError for not-found conditions (caller maps to 404).
- No business logic — just DB read/write. Callers own commit/rollback.
- Soft delete only: archive() sets status=archived, never deletes.
- Latest-run embedding uses a LATERAL JOIN, not N+1. Mirrors OP1 pattern.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders.models import Agent
from artemis.integrations.models import Integration
from artemis.pipelines.models import Pipeline, PipelineAIConversation, PipelineRun
from artemis.pipelines.schemas import ConnectorRequirement, PipelineExportBundle

_SENSITIVE_KEYS = ("api_key", "secret", "token", "password", "credential")
_CONNECTOR_FIELDS = {
    "starbridge": ["api_key", "api_url"],
    "slack": ["bot_token"],
    "gcal": ["client_id", "client_secret", "refresh_token"],
    "gmail": ["client_id", "client_secret", "refresh_token"],
    "jira": ["base_url", "email", "api_token"],
    "granola": ["api_key"],
}

# ── Pipeline CRUD ─────────────────────────────────────────────────────────────


async def create_pipeline(session: AsyncSession, **kwargs: Any) -> Pipeline:
    pipeline_id = kwargs.pop("id", None) or str(uuid.uuid4())
    if "metadata" in kwargs:
        kwargs["metadata_"] = kwargs.pop("metadata")
    p = Pipeline(id=pipeline_id, **kwargs)
    session.add(p)
    await session.flush()
    await session.refresh(p)
    return p


async def get_pipeline(session: AsyncSession, pipeline_id: str) -> Pipeline:
    result = await session.execute(select(Pipeline).where(Pipeline.id == pipeline_id).limit(1))
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"Pipeline '{pipeline_id}' not found")
    return row


async def list_pipelines(
    session: AsyncSession,
    *,
    status: str | None = None,
    owner_user_id: int | None = None,
    has_trigger: bool | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> list[tuple[Pipeline, PipelineRun | None]]:
    """List pipelines with latest_run embedded via a LATERAL subquery.

    Returns list of (Pipeline, PipelineRun|None) tuples — single query.
    Excludes archived by default unless status='archived' is requested.
    """
    run_alias = PipelineRun.__table__.alias("latest_run")
    lateral_sq = (
        select(run_alias)
        .where(run_alias.c.pipeline_id == Pipeline.id)
        .order_by(run_alias.c.created_at.desc())
        .limit(1)
        .correlate(Pipeline.__table__)
        .lateral("latest_run")
    )

    q = (
        select(Pipeline, lateral_sq)
        .outerjoin(lateral_sq, text("true"))
        .order_by(Pipeline.created_at.desc())
        .limit(limit)
    )

    q = q.where(Pipeline.status == status) if status else q.where(Pipeline.status != "archived")

    if owner_user_id is not None:
        q = q.where(Pipeline.owner_user_id == owner_user_id)

    if has_trigger is True:
        q = q.where(Pipeline.trigger_config.isnot(None))
    elif has_trigger is False:
        q = q.where(Pipeline.trigger_config.is_(None))

    if cursor:
        q = q.where(Pipeline.created_at < text(f"'{cursor}'::timestamptz"))

    result = await session.execute(q)
    pairs: list[tuple[Pipeline, PipelineRun | None]] = []
    run_col_names = [c.name for c in run_alias.c]
    for row in result.all():
        p_obj: Pipeline = row[0]
        if row[1] is None:
            pairs.append((p_obj, None))
        else:
            run_data = dict(zip(run_col_names, row[1:], strict=False))
            if "metadata" in run_data:
                run_data["metadata_"] = run_data.pop("metadata")
            run_obj = PipelineRun(**run_data)
            pairs.append((p_obj, run_obj))
    return pairs


async def update_pipeline(session: AsyncSession, pipeline_id: str, **kwargs: Any) -> Pipeline:
    p = await get_pipeline(session, pipeline_id)
    for key, val in kwargs.items():
        col = "metadata_" if key == "metadata" else key
        setattr(p, col, val)
    p.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(p)
    return p


async def archive_pipeline(session: AsyncSession, pipeline_id: str) -> Pipeline:
    """Soft delete: set status=archived. Row stays in table."""
    p = await get_pipeline(session, pipeline_id)
    p.status = "archived"
    p.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(p)
    return p


async def permanently_delete_pipeline(session: AsyncSession, pipeline_id: str) -> None:
    p = await get_pipeline(session, pipeline_id)
    if p.status != "archived":
        raise RuntimeError("Pipeline must be archived before permanent deletion")
    await session.execute(delete(Pipeline).where(Pipeline.id == pipeline_id))
    await session.flush()


def _scrub_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _scrub_sensitive(v)
            for k, v in value.items()
            if not any(part in str(k).lower() for part in _SENSITIVE_KEYS)
        }
    if isinstance(value, list):
        return [_scrub_sensitive(v) for v in value]
    return value


def _collect_agent_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"agent_id", "agentId"} and isinstance(nested, str):
                found.add(nested)
            else:
                found.update(_collect_agent_ids(nested))
    elif isinstance(value, list):
        for item in value:
            found.update(_collect_agent_ids(item))
    return found


def _collect_connector_kinds(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {
                "connector_kind",
                "connectorKind",
                "integration_provider",
            } and isinstance(nested, str):
                found.add(nested)
            found.update(_collect_connector_kinds(nested))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str) and "." in item:
                found.add(item.split(".", 1)[0])
            else:
                found.update(_collect_connector_kinds(item))
    return found


async def build_export_bundle(
    session: AsyncSession,
    pipeline_id: str,
    *,
    exported_from: str | None = None,
) -> PipelineExportBundle:
    p = await get_pipeline(session, pipeline_id)
    pipeline = {
        "name": p.name,
        "description": p.description,
        "nodes": _scrub_sensitive(p.nodes or []),
        "edges": _scrub_sensitive(p.edges or []),
        "trigger_config": _scrub_sensitive(p.trigger_config),
        "status": p.status,
        "metadata": _scrub_sensitive(p.metadata_ or {}),
    }

    agent_ids = _collect_agent_ids(p.nodes or [])
    agents: list[dict[str, Any]] = []
    if agent_ids:
        result = await session.execute(
            select(Agent).where(Agent.agent_id.in_(agent_ids)).order_by(Agent.agent_id)
        )
        for a in result.scalars().all():
            agents.append(
                {
                    "agent_id": a.agent_id,
                    "name": a.name,
                    "description": a.description,
                    "goal": a.goal,
                    "system_prompt": a.system_prompt,
                    "tools": _scrub_sensitive(a.tools or []),
                    "persona": _scrub_sensitive(a.persona),
                    "model": a.model,
                    "provider": a.provider,
                    "fallback_provider": a.fallback_provider,
                    "fallback_model": a.fallback_model,
                    "memory_policy": a.memory_policy,
                    "permission_mode": a.permission_mode,
                    "output_contract": _scrub_sensitive(a.output_contract),
                    "metadata": _scrub_sensitive(a.metadata_ or {}),
                }
            )

    connector_kinds = _collect_connector_kinds(p.nodes or [])
    connector_kinds.update(_collect_connector_kinds([a["tools"] for a in agents]))
    connectors = [
        ConnectorRequirement(
            kind=kind,
            label=f"Required for tools or nodes that call {kind}.*",
            fields_needed=_CONNECTOR_FIELDS.get(kind, []),
        )
        for kind in sorted(connector_kinds)
    ]

    return PipelineExportBundle(
        format_version="1",
        exported_at=datetime.now(UTC),
        exported_from=exported_from,
        pipeline=pipeline,
        agents_required=agents,
        connectors_required=connectors,
    )


async def import_bundle(
    session: AsyncSession,
    bundle: PipelineExportBundle,
) -> dict[str, Any]:
    agents_created: list[str] = []
    agents_skipped: list[str] = []
    for agent in bundle.agents_required:
        existing = await session.execute(
            select(Agent.agent_id).where(Agent.agent_id == agent.agent_id).limit(1)
        )
        if existing.scalar_one_or_none():
            agents_skipped.append(agent.agent_id)
            continue
        values = agent.model_dump()
        values["metadata_"] = values.pop("metadata", {})
        session.add(Agent(**values))
        agents_created.append(agent.agent_id)

    warnings: list[str] = []
    for req in bundle.connectors_required:
        active = await session.execute(
            select(Integration.id)
            .where(Integration.provider == req.kind, Integration.status == "active")
            .limit(1)
        )
        if active.scalar_one_or_none() is None:
            warnings.append(f"No {req.kind} connector configured; create one before running")

    source = bundle.pipeline
    metadata = dict(source.get("metadata") or {})
    if warnings:
        metadata["import_warnings"] = warnings
    requested_status = (
        source.get("status") if source.get("status") in {"active", "paused"} else "active"
    )
    pipeline = await create_pipeline(
        session,
        name=source["name"],
        description=source.get("description"),
        nodes=source.get("nodes") or [],
        edges=source.get("edges") or [],
        trigger_config=source.get("trigger_config"),
        status="paused" if warnings else requested_status,
        metadata_=metadata,
    )
    await session.flush()
    return {
        "pipeline_id": pipeline.id,
        "agents_created": agents_created,
        "agents_skipped": agents_skipped,
        "import_warnings": warnings,
    }


async def get_pipeline_with_latest_run(
    session: AsyncSession, pipeline_id: str
) -> tuple[Pipeline, PipelineRun | None]:
    run_alias = PipelineRun.__table__.alias("latest_run_detail")
    lateral_sq = (
        select(run_alias)
        .where(run_alias.c.pipeline_id == Pipeline.id)
        .order_by(run_alias.c.created_at.desc())
        .limit(1)
        .correlate(Pipeline.__table__)
        .lateral("latest_run_detail")
    )
    q = (
        select(Pipeline, lateral_sq)
        .outerjoin(lateral_sq, text("true"))
        .where(Pipeline.id == pipeline_id)
        .limit(1)
    )
    result = await session.execute(q)
    row = result.first()
    if row is None:
        raise ValueError(f"Pipeline '{pipeline_id}' not found")
    p_obj: Pipeline = row[0]
    if row[1] is None:
        return (p_obj, None)
    run_col_names = [c.name for c in run_alias.c]
    run_data = dict(zip(run_col_names, row[1:], strict=False))
    if "metadata" in run_data:
        run_data["metadata_"] = run_data.pop("metadata")
    run_obj = PipelineRun(**run_data)
    return (p_obj, run_obj)


# ── Pipeline runs ─────────────────────────────────────────────────────────────


async def create_pipeline_run(session: AsyncSession, **kwargs: Any) -> PipelineRun:
    run_id = kwargs.pop("id", None) or str(uuid.uuid4())
    if "metadata" in kwargs:
        kwargs["metadata_"] = kwargs.pop("metadata")
    run = PipelineRun(id=run_id, **kwargs)
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def get_pipeline_run(session: AsyncSession, run_id: str) -> PipelineRun:
    result = await session.execute(select(PipelineRun).where(PipelineRun.id == run_id).limit(1))
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"PipelineRun '{run_id}' not found")
    return row


async def list_pipeline_runs(
    session: AsyncSession,
    pipeline_id: str,
    *,
    limit: int = 30,
    cursor: str | None = None,
) -> list[PipelineRun]:
    q = (
        select(PipelineRun)
        .where(PipelineRun.pipeline_id == pipeline_id)
        .order_by(PipelineRun.created_at.desc())
        .limit(limit)
    )
    if cursor:
        q = q.where(PipelineRun.created_at < text(f"'{cursor}'::timestamptz"))
    result = await session.execute(q)
    return list(result.scalars().all())


async def list_all_pipeline_runs(
    session: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> list[PipelineRun]:
    """Return recent pipeline runs across all pipelines (for run history page)."""
    q = select(PipelineRun).order_by(PipelineRun.created_at.desc()).limit(limit)
    if status:
        q = q.where(PipelineRun.status == status)
    if cursor:
        q = q.where(PipelineRun.created_at < text(f"'{cursor}'::timestamptz"))
    result = await session.execute(q)
    return list(result.scalars().all())


# ── Pipeline AI Conversations ─────────────────────────────────────────────────


async def get_or_create_ai_conversation(
    session: AsyncSession, pipeline_id: str
) -> PipelineAIConversation:
    """Return the AI conversation row for a pipeline, creating it if absent."""
    result = await session.execute(
        select(PipelineAIConversation)
        .where(PipelineAIConversation.pipeline_id == pipeline_id)
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = PipelineAIConversation(pipeline_id=pipeline_id, conversation=[])
        session.add(row)
        await session.flush()
        await session.refresh(row)
    return row


async def append_ai_message(
    session: AsyncSession,
    pipeline_id: str,
    role: str,
    content: str,
) -> PipelineAIConversation:
    """Append a message to the pipeline's AI conversation history."""
    from datetime import UTC, datetime

    row = await get_or_create_ai_conversation(session, pipeline_id)
    conversation: list[dict[str, Any]] = list(row.conversation or [])
    conversation.append({"role": role, "content": content})
    row.conversation = conversation
    row.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(row)
    return row


async def clear_ai_conversation(session: AsyncSession, pipeline_id: str) -> PipelineAIConversation:
    """Clear the conversation history for a pipeline (e.g. on user request)."""
    from datetime import UTC, datetime

    row = await get_or_create_ai_conversation(session, pipeline_id)
    row.conversation = []
    row.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(row)
    return row


async def update_pipeline_run(session: AsyncSession, run_id: str, **kwargs: Any) -> PipelineRun:
    from sqlalchemy.orm.attributes import flag_modified

    run = await get_pipeline_run(session, run_id)
    for key, val in kwargs.items():
        col = "metadata_" if key == "metadata" else key
        setattr(run, col, val)
        # SQLAlchemy won't track in-place mutations on plain JSONB dicts;
        # flag_modified forces the column to be included in the next flush.
        if col in ("node_states", "metadata_"):
            flag_modified(run, col)
    await session.flush()
    await session.refresh(run)
    return run
