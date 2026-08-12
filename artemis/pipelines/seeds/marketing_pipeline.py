from __future__ import annotations

from json import dumps
from typing import Any

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import DeliverableType
from artemis.pipelines.schemas import PipelineCreate

PIPELINE_ID = "marketing.main"
CAMPAIGN_DELIVERABLES_PIPELINE_ID = "marketing.campaign_deliverables"
TRIGGER_CONFIG = {"type": "scheduled", "cron": "0 */4 * * *", "timezone": "America/Chicago"}
MANUAL_TRIGGER_CONFIG = {"type": "manual"}
SCOUT_SLUGS = "starbridge_researcher regional_news linkedin_observer legislative federal_funding state_doe procurement board_minutes leadership_transition".split()  # noqa: SIM905
DOWNSTREAM = (
    ("marketing.qualifier.cross_reference", "qualifier_cross_reference", 420),
    ("marketing.qualifier.brief_composer", "qualifier_brief_composer", 560),
    ("marketing.content.brief_assembler", "content_brief_assembler", 820),
    ("marketing.content.asset_selector", "content_asset_selector", 960),
    ("marketing.content.writing_studio_adapter", "content_writing_studio_adapter", 1100),
)
AGENT_IDS = tuple([f"marketing.scout.{slug}" for slug in SCOUT_SLUGS] + [d[0] for d in DOWNSTREAM])
_DEFAULT_ACTIVE_DELIVERABLES = (
    {"slug": "outreach_email", "label": "Outreach Email", "display_order": 1},
)


def _label(slug: str) -> str:
    return slug.replace("_", " ").title().replace("Doe", "DoE")


def _agent_node(agent_id: str, node_id: str, x: int, y: int) -> dict[str, Any]:
    label_slug = node_id.removeprefix("scout_").removeprefix("qualifier_").removeprefix("content_")
    return {
        "id": node_id,
        "type": "agent_invocation",
        "label": _label(label_slug),
        "config": {"agent_id": agent_id, "mode": "scheduled"},
        "position": {"x": float(x), "y": float(y)},
    }


def _scout_node(agent_id: str, node_id: str, x: int, y: int) -> dict[str, Any]:
    """Scout variant of _agent_node with continue_on_failure=true.

    Scout nodes are independent parallel feeders — a single timeout/403/network
    error must NOT nuke the whole run.  The gate reads signals by pipeline_run_id
    and is unaffected by which individual scout node produced them.
    """
    node = _agent_node(agent_id, node_id, x, y)
    node["config"]["continue_on_failure"] = True
    return node


def _deliverable_node(node_id: str, label: str, deliverable_slug: str, x: int) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "agent_invocation",
        "label": label,
        "config": {
            "agent_id": "marketing.content.writing_studio_adapter",
            "mode": "scheduled",
            "deliverable_type_slug": deliverable_slug,
            "cost_cap_usd": 1.0,
        },
        "position": {"x": float(x), "y": 1260.0},
    }


def _gate_2_node() -> dict[str, Any]:
    return {
        "id": "gate_2_approval_drawer",
        "type": "human_gate",
        "label": "Gate 2 Approval Drawer",
        "config": {
            "approval_kind": "content_draft",
            "approvers": ["joshua.mukai@amiralearning.com", "angela.miata@amiralearning.com"],
            "timeout_hours": 72,
            "on_timeout": "escalate",
            "escalation_to": ["jon.fila@amiralearning.com"],
        },
        "position": {"x": 800.0, "y": 1440.0},
    }


def _deliverable_definitions(
    deliverable_types: list[dict[str, Any]] | None = None,
) -> list[tuple[str, str, str, int]]:
    rows = deliverable_types or list(_DEFAULT_ACTIVE_DELIVERABLES)
    ordered = sorted(rows, key=lambda row: int(row.get("display_order", 0)))
    return [
        (
            f"deliverable_{row['slug']}",
            str(row.get("label") or row["slug"]).title(),
            str(row["slug"]),
            420 + (i * 220),
        )
        for i, row in enumerate(ordered)
    ]


def build_marketing_pipeline(
    deliverable_types: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {
            "id": "trigger_scheduled",
            "type": "trigger_scheduled",
            "label": "Every 4 Hours",
            "config": TRIGGER_CONFIG,
            "position": {"x": 800.0, "y": 40.0},
        }
    ]
    nodes += [
        _scout_node(f"marketing.scout.{slug}", f"scout_{slug}", 120 + i * 170, 220)
        for i, slug in enumerate(SCOUT_SLUGS)
    ]
    nodes += [_agent_node(agent_id, node_id, 800, y) for agent_id, node_id, y in DOWNSTREAM[:2]]
    nodes[-2]["label"] = "Cross-Reference (Phase 1→2→3)"
    nodes[-2]["config"]["description"] = (
        "Hard filters → Score against all rulesets → Route to top campaign type(s)"
    )
    nodes.append(
        {
            "id": "gate_1_signals_inbox",
            "type": "human_gate",
            "label": "Gate 1 Signals Inbox",
            "config": {
                "approval_kind": "signal_brief",
                "approvers": ["joshua.mukai@amiralearning.com", "angela.miata@amiralearning.com"],
                "timeout_hours": 72,
            },
            "position": {"x": 800.0, "y": 690.0},
        }
    )
    nodes.append(_agent_node(DOWNSTREAM[2][0], DOWNSTREAM[2][1], 800, DOWNSTREAM[2][2]))
    nodes[-1]["config"]["propose_initiation"] = True

    scouts = [f"scout_{slug}" for slug in SCOUT_SLUGS]
    linear = [
        "qualifier_cross_reference",
        "qualifier_brief_composer",
        "gate_1_signals_inbox",
        "content_brief_assembler",
    ]
    pairs = [("trigger_scheduled", scout) for scout in scouts]
    pairs += [(scout, "qualifier_cross_reference") for scout in scouts]
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
        "name": "Marketing Pipeline",
        "description": "Runs the canonical Marketing OS signal-to-draft flow.",
        "nodes": nodes,
        "edges": edges,
        "trigger_config": TRIGGER_CONFIG,
        "status": "active",
    }
    PipelineCreate(**{k: v for k, v in pipeline.items() if k != "id"})
    return pipeline


def build_campaign_deliverables_pipeline(
    deliverable_types: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    deliverables = _deliverable_definitions(deliverable_types)
    nodes: list[dict[str, Any]] = [
        {
            "id": "trigger_manual",
            "type": "trigger_manual",
            "label": "Manual Trigger",
            "config": MANUAL_TRIGGER_CONFIG,
            "position": {"x": 800.0, "y": 40.0},
        },
        _agent_node(DOWNSTREAM[3][0], DOWNSTREAM[3][1], 800, DOWNSTREAM[3][2]),
        _agent_node(DOWNSTREAM[4][0], DOWNSTREAM[4][1], 800, DOWNSTREAM[4][2]),
    ]
    nodes += [
        _deliverable_node(node_id, label, deliverable_slug, x)
        for node_id, label, deliverable_slug, x in deliverables
    ]
    nodes.append(_gate_2_node())

    pairs: list[tuple[str, str]] = [
        ("trigger_manual", "content_asset_selector"),
        ("content_asset_selector", "content_writing_studio_adapter"),
    ]
    pairs += [("content_writing_studio_adapter", node_id) for node_id, *_ in deliverables]
    pairs += [(node_id, "gate_2_approval_drawer") for node_id, *_ in deliverables]
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
        "id": CAMPAIGN_DELIVERABLES_PIPELINE_ID,
        "name": "Marketing Campaign Deliverables",
        "description": "Runs campaign deliverables on demand for one initiated campaign.",
        "nodes": nodes,
        "edges": edges,
        "trigger_config": MANUAL_TRIGGER_CONFIG,
        "status": "active",
    }
    PipelineCreate(**{k: v for k, v in pipeline.items() if k != "id"})
    return pipeline


async def _missing_agent_ids(session: AsyncSession) -> list[str]:
    stmt = text("SELECT agent_id FROM agents WHERE agent_id IN :agent_ids").bindparams(
        bindparam("agent_ids", expanding=True)
    )
    found = set((await session.execute(stmt, {"agent_ids": AGENT_IDS})).scalars().all())
    return [agent_id for agent_id in AGENT_IDS if agent_id not in found]


async def _upsert_pipeline(session: AsyncSession, row: dict[str, Any]) -> bool:
    cursor = await session.execute(
        text(
            """
            INSERT INTO pipelines
                (id, name, description, nodes, edges, trigger_config, status, updated_at)
            VALUES (:id, :name, :description, CAST(:nodes AS jsonb), CAST(:edges AS jsonb),
                    CAST(:trigger_config AS jsonb), :status, now())
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, description = EXCLUDED.description, nodes = EXCLUDED.nodes,
                edges = EXCLUDED.edges, trigger_config = EXCLUDED.trigger_config,
                status = EXCLUDED.status, updated_at = now()
            RETURNING (xmax = 0) AS inserted
            """
        ),
        {**row, **{k: dumps(row[k]) for k in ("nodes", "edges", "trigger_config")}},
    )
    return bool(cursor.scalar_one())


async def seed_marketing_pipeline(session: AsyncSession) -> dict[str, Any]:
    if missing := await _missing_agent_ids(session):
        raise RuntimeError(
            "Marketing pipeline seed requires M5 marketing agents first. "
            "Run scripts/seed_marketing_agents.py; missing agent_ids: " + ", ".join(missing)
        )

    deliverable_rows = await session.execute(
        select(DeliverableType)
        .where(DeliverableType.active.is_(True))
        .order_by(DeliverableType.display_order, DeliverableType.id)
    )
    deliverable_types = [
        {"slug": row.slug, "label": row.label, "display_order": row.display_order}
        for row in deliverable_rows.scalars().all()
    ]
    discovery = build_marketing_pipeline(deliverable_types)
    deliverables = build_campaign_deliverables_pipeline(deliverable_types)
    inserted_count = int(await _upsert_pipeline(session, discovery)) + int(
        await _upsert_pipeline(session, deliverables)
    )
    await session.commit()
    return {
        "inserted": inserted_count,
        "pipelines": {
            PIPELINE_ID: {
                "node_count": len(discovery["nodes"]),
                "edge_count": len(discovery["edges"]),
            },
            CAMPAIGN_DELIVERABLES_PIPELINE_ID: {
                "node_count": len(deliverables["nodes"]),
                "edge_count": len(deliverables["edges"]),
            },
        },
    }


async def run_seed() -> None:
    from artemis.db import SessionLocal

    async with SessionLocal() as session:
        result = await seed_marketing_pipeline(session)
        print(f"seed_marketing_pipeline: inserted={result['inserted']}")
        for pipeline_id, counts in result["pipelines"].items():
            print(
                f"{pipeline_id}: node_count={counts['node_count']} edge_count={counts['edge_count']}"
            )
