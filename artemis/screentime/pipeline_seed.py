"""Register a Pipeline row for Screen-Time Watch so it shows on the pipelines page.

This pipeline is **display-only**: the actual work runs in ``runner.py`` (a
dedicated cron runner, deliberately NOT driven by the shared PipelineExecutor —
see runner.py for why). The seeded row gives the required pipelines-page
visibility, isolated from the marketing campaign pipeline (distinct id +
metadata flag).

The node graph documents the real flow (fan-out → dedupe+filter → classify →
store → rollup) using ``skill_call`` display nodes; the executor never runs them.
"""

from __future__ import annotations

from json import dumps
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.pipelines.schemas import PipelineCreate
from artemis.screentime import PIPELINE_ID


def _node(
    node_id: str, node_type: str, label: str, x: int, y: int, **config: Any
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "label": label,
        "config": config,
        "position": {"x": float(x), "y": float(y)},
    }


def build_screentime_pipeline() -> dict[str, Any]:
    """Build the display pipeline definition (validated via PipelineCreate)."""
    from artemis.config import settings
    from artemis.screentime.scout_fanout import SCREENTIME_KEYWORDS

    trigger_config = {
        "type": "scheduled",
        "cron": settings.screentime_cron,
        "timezone": settings.screentime_cron_tz,
    }

    scout_labels = ["legislative", "state_doe", "board_minutes", "regional_news"]
    nodes: list[dict[str, Any]] = [
        _node(
            "trigger_scheduled",
            "trigger_scheduled",
            "National Sweep (cron)",
            700,
            40,
            **trigger_config,
        ),
    ]
    # Scout fan-out nodes (national, screen-time tuned) — display only.
    nodes += [
        _node(
            f"scout_{slug}",
            "skill_call",
            f"Scout: {slug.replace('_', ' ').title()}",
            120 + i * 320,
            220,
            scope="national_50_states_plus_dc",
            topic="instructional_screen_time",
            note="read-only in-process gather; NOT campaign-scoped",
        )
        for i, slug in enumerate(scout_labels)
    ]
    nodes += [
        _node(
            "filter_real_moves",
            "skill_call",
            "Dedupe + Real-Moves Filter",
            700,
            420,
            dedupe_on="content_hash",
            keep="proposed|passed|amended|guidance",
            drop="headlines/opinion + cellphone-ban (out of lane)",
        ),
        _node(
            "classify_stance",
            "skill_call",
            "Stance + Amira Angle (config-driven)",
            700,
            600,
            provider="codex→claude-code",
            note="tool-less, off Opus; rules in screentime_stance_config",
            keywords=SCREENTIME_KEYWORDS,
        ),
        _node(
            "store_signals",
            "skill_call",
            "Store → screentime_signals",
            700,
            780,
            table="screentime_signals",
        ),
        _node(
            "rollup_state_stance",
            "skill_call",
            "Recompute Per-State Stance",
            700,
            960,
            table="screentime_state_stance",
        ),
    ]

    linear = [
        "filter_real_moves",
        "classify_stance",
        "store_signals",
        "rollup_state_stance",
    ]
    pairs: list[tuple[str, str]] = [("trigger_scheduled", f"scout_{s}") for s in scout_labels]
    pairs += [(f"scout_{s}", "filter_real_moves") for s in scout_labels]
    pairs += list(zip(linear, linear[1:], strict=False))
    edges = [
        {
            "id": f"edge_{source}_to_{target}",
            "source_node_id": source,
            "target_node_id": target,
            "condition": None,
            "data_shape": None,
        }
        for source, target in pairs
    ]

    pipeline = {
        "id": PIPELINE_ID,
        "name": "Screen-Time Watch",
        "description": (
            "National screen-time legislation/policy intelligence. Runs on its own "
            "cron runner (artemis/screentime/runner.py); isolated screentime_* data, "
            "separate from the marketing campaign pipeline."
        ),
        "nodes": nodes,
        "edges": edges,
        "trigger_config": trigger_config,
        "status": "active",
        "metadata": {"isolated": True, "namespace": "screentime", "display_only": True},
    }
    PipelineCreate(**{k: v for k, v in pipeline.items() if k != "id"})
    return pipeline


async def seed_screentime_pipeline(session: AsyncSession) -> dict[str, Any]:
    """Upsert the display pipeline row. Idempotent. Caller commits."""
    row = build_screentime_pipeline()
    cursor = await session.execute(
        text(
            """
            INSERT INTO pipelines
                (id, name, description, nodes, edges, trigger_config, status, metadata, updated_at)
            VALUES (:id, :name, :description, CAST(:nodes AS jsonb), CAST(:edges AS jsonb),
                    CAST(:trigger_config AS jsonb), :status, CAST(:metadata AS jsonb), now())
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, description = EXCLUDED.description, nodes = EXCLUDED.nodes,
                edges = EXCLUDED.edges, trigger_config = EXCLUDED.trigger_config,
                status = EXCLUDED.status, metadata = EXCLUDED.metadata, updated_at = now()
            RETURNING (xmax = 0) AS inserted
            """
        ),
        {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "nodes": dumps(row["nodes"]),
            "edges": dumps(row["edges"]),
            "trigger_config": dumps(row["trigger_config"]),
            "status": row["status"],
            "metadata": dumps(row["metadata"]),
        },
    )
    inserted = bool(cursor.scalar_one())
    return {
        "pipeline_id": row["id"],
        "inserted": inserted,
        "node_count": len(row["nodes"]),
        "edge_count": len(row["edges"]),
    }
