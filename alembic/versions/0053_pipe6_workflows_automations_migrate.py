"""PIPE6 — sunset Workflows/Automations and migrate workflow mirror to Pipelines.

Revision ID: 0053
Revises: 0052
Create Date: 2026-05-30
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "0053"
down_revision: str = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PLATFORM_SCOPE_KIND = "workspace"
_PLATFORM_SCOPE_ID = "platform"


def _slug_step(index: int) -> str:
    return f"workflow_step_{index + 1}"


def _build_nodes(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = [
        {
            "id": "manual_trigger",
            "type": "trigger_manual",
            "label": "Manual Trigger",
            "config": {"type": "manual"},
            "position": {"x": 120, "y": 80},
        }
    ]
    for index, step in enumerate(steps):
        nodes.append(
            {
                "id": _slug_step(index),
                "type": "skill_call",
                "label": str(step.get("label") or f"Step {index + 1}"),
                "config": {
                    "prompt": step.get("prompt"),
                    "legacy_workflow_step": step,
                    "migration_note": "Migrated from legacy workflows.steps by PIPE6.",
                },
                "position": {"x": 120, "y": 220 + (index * 140)},
            }
        )
    return nodes


def _build_edges(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not steps:
        return []
    edges: list[dict[str, Any]] = []
    previous = "manual_trigger"
    for index, _step in enumerate(steps):
        current = _slug_step(index)
        edges.append(
            {
                "id": f"edge_{previous}_to_{current}",
                "source_node_id": previous,
                "target_node_id": current,
                "condition": None,
                "data_shape": None,
            }
        )
        previous = current
    return edges


def _write_pipe6_memory_observation(bind: sa.engine.Connection, migrated_count: int) -> None:
    """Best-effort MC-style migration observation; never blocks schema upgrade."""
    try:
        content = (
            "PIPE6 migration executed D6 Pipeline-unification lock on "
            f"{datetime.now(UTC).isoformat(timespec='seconds')}. "
            f"Migrated workflows to paused Pipeline mirror rows: {migrated_count}. "
            "Deprecated routes now return 410: /api/automations*, "
            "/api/automation-runs*, /api/workflows*. Original workflow and "
            "workflow_run rows were preserved per the lossless invariant."
        )
        content_hash = hashlib.sha256(
            f"{_PLATFORM_SCOPE_KIND}:{_PLATFORM_SCOPE_ID}:{content}".encode()
        ).hexdigest()
        bind.execute(
            sa.text(
                """
                INSERT INTO memory_scopes (scope_kind, scope_id, display_name)
                VALUES (:scope_kind, :scope_id, :display_name)
                ON CONFLICT (scope_kind, scope_id) DO NOTHING
                """
            ),
            {
                "scope_kind": _PLATFORM_SCOPE_KIND,
                "scope_id": _PLATFORM_SCOPE_ID,
                "display_name": "Platform",
            },
        )
        result = bind.execute(
            sa.text(
                """
                INSERT INTO memory_observations (
                    scope_kind, scope_id, category, content, content_hash,
                    source_quality, user_confirmed, confidence, wing,
                    confidence_origin
                )
                VALUES (
                    :scope_kind, :scope_id, 'pipe6_migration', :content,
                    :content_hash, 1.0, TRUE, 1.0, 'durable',
                    'mc_pipe6_migration'
                )
                ON CONFLICT ON CONSTRAINT uq_obs_scope_hash DO NOTHING
                RETURNING id
                """
            ),
            {
                "scope_kind": _PLATFORM_SCOPE_KIND,
                "scope_id": _PLATFORM_SCOPE_ID,
                "content": content,
                "content_hash": content_hash,
            },
        )
        obs_id = result.scalar_one_or_none()
        if obs_id is None:
            obs_id = bind.execute(
                sa.text(
                    """
                    SELECT id
                    FROM memory_observations
                    WHERE scope_kind = :scope_kind
                      AND scope_id = :scope_id
                      AND content_hash = :content_hash
                    """
                ),
                {
                    "scope_kind": _PLATFORM_SCOPE_KIND,
                    "scope_id": _PLATFORM_SCOPE_ID,
                    "content_hash": content_hash,
                },
            ).scalar_one_or_none()
        if obs_id is not None:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO memory_observation_scopes (
                        observation_id, scope_kind, scope_id, weight, is_primary
                    )
                    VALUES (:obs_id, :scope_kind, :scope_id, 1.0, TRUE)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "obs_id": obs_id,
                    "scope_kind": _PLATFORM_SCOPE_KIND,
                    "scope_id": _PLATFORM_SCOPE_ID,
                },
            )
    except Exception:
        pass


def upgrade() -> None:
    bind = op.get_bind()
    workflows = bind.execute(
        sa.text(
            """
            SELECT id, workflow_id, name, description, steps, owner_user_id, created_at
            FROM workflows
            ORDER BY id
            """
        )
    ).mappings()

    migrated_count = 0
    for workflow in workflows:
        pipeline_id = f"migrated-from-workflow-{workflow['id']}"
        exists = bind.execute(
            sa.text("SELECT 1 FROM pipelines WHERE id = :pipeline_id"),
            {"pipeline_id": pipeline_id},
        ).scalar_one_or_none()
        if exists:
            continue

        steps = workflow["steps"] if isinstance(workflow["steps"], list) else []
        bind.execute(
            sa.text(
                """
                INSERT INTO pipelines (
                    id, name, description, nodes, edges, trigger_config,
                    status, owner_user_id, metadata, created_at, updated_at
                )
                VALUES (
                    :id, :name, :description, CAST(:nodes AS jsonb),
                    CAST(:edges AS jsonb), CAST(:trigger_config AS jsonb),
                    'paused', :owner_user_id, CAST(:metadata AS jsonb),
                    :created_at, now()
                )
                """
            ),
            {
                "id": pipeline_id,
                "name": f"{workflow['name']} (migrated from workflow)",
                "description": workflow["description"],
                "nodes": json.dumps(_build_nodes(steps)),
                "edges": json.dumps(_build_edges(steps)),
                "trigger_config": json.dumps({"type": "manual"}),
                "owner_user_id": workflow["owner_user_id"],
                "metadata": json.dumps(
                    {
                        "pipe6_migrated_from": {
                            "table": "workflows",
                            "id": workflow["id"],
                            "workflow_id": workflow["workflow_id"],
                        },
                        "schema_compatibility": (
                            "workflows.steps transformed to pipelines.nodes/edges; "
                            "legacy prompt steps preserved in node config."
                        ),
                    }
                ),
                "created_at": workflow["created_at"],
            },
        )
        migrated_count += 1

    _write_pipe6_memory_observation(bind, migrated_count)

    op.execute(
        """
        COMMENT ON TABLE workflows IS
        'DEPRECATED 2026-05-30 PIPE6 — superseded by pipelines table (D6 lock).
        Rows preserved per lossless invariant. Do not write new rows.'
        """
    )
    op.execute(
        """
        COMMENT ON TABLE workflow_runs IS
        'DEPRECATED 2026-05-30 PIPE6 — superseded by pipeline_runs.
        Rows preserved. Do not write new rows.'
        """
    )
    op.execute(
        """
        COMMENT ON TABLE automations IS
        'DEPRECATED 2026-05-30 PIPE6 — never had production usage.
        Empty table preserved. Schema may be dropped in a future migration after grace period.'
        """
    )
    op.execute(
        """
        COMMENT ON TABLE automation_runs IS
        'DEPRECATED 2026-05-30 PIPE6 — never had production usage.
        Empty table preserved.'
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM pipelines WHERE id LIKE 'migrated-from-workflow-%'")
    op.execute("COMMENT ON TABLE workflows IS NULL")
    op.execute("COMMENT ON TABLE workflow_runs IS NULL")
    op.execute("COMMENT ON TABLE automations IS NULL")
    op.execute("COMMENT ON TABLE automation_runs IS NULL")
