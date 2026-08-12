from __future__ import annotations

from json import dumps
from typing import Any

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import DeliverableType
from artemis.pipelines.schemas import PipelineCreate

PIPELINE_ID = "marketing.main"
CAMPAIGN_DELIVERABLES_PIPELINE_ID = "marketing.campaign_deliverables"
# Daily at 06:00 Chicago (11:00 UTC / 07:00 ET), two hours ahead of the daily
# market-signals brief, so qualification is fresh when the brief is composed.
#
# Was every 4 hours (6 runs/day at ~$0.90 each, ~$164/month). Cut to daily on
# 2026-08-12 for two reasons. First, delivery is now daily: with Gate 1 removed,
# the pipeline's output is read once a day in Callie's brief, so five of six runs
# produced signals nobody saw sooner. Second, and larger: the nine scouts this
# pipeline invokes ALSO run standalone on their own 24-hour cadence
# (artemis/marketing/scout_scheduler.py, DEFAULT_CADENCE_SECONDS = 86400), and
# those standalone runs are what write and qualify the great majority of signals
# -- 141 qualified in three days while this pipeline sat blocked and ran not
# once. Six pipeline runs a day were re-invoking the same nine scouts the
# scheduler had already run.
TRIGGER_CONFIG = {"type": "scheduled", "cron": "0 6 * * *", "timezone": "America/Chicago"}
MANUAL_TRIGGER_CONFIG = {"type": "manual"}
SCOUT_SLUGS = "starbridge_researcher regional_news linkedin_observer legislative federal_funding state_doe procurement board_minutes leadership_transition".split()  # noqa: SIM905
DOWNSTREAM = (
    ("marketing.qualifier.cross_reference", "qualifier_cross_reference", 420),
    ("marketing.qualifier.brief_composer", "qualifier_brief_composer", 560),
    ("marketing.content.brief_assembler", "content_brief_assembler", 820),
    ("marketing.content.asset_selector", "content_asset_selector", 960),
    ("marketing.content.writing_studio_adapter", "content_writing_studio_adapter", 1100),
)
# Agents the two seeded pipelines actually invoke, and therefore the ones whose
# absence must block seeding. DOWNSTREAM[2] (marketing.content.brief_assembler)
# is deliberately excluded: its node left marketing.main with Gate 1 on
# 2026-08-12 (see build_marketing_pipeline), and the deliverables pipeline never
# used it. The agent itself stays defined and available for the human-started
# campaign-initiation flow; it simply must not be a seeding precondition while
# nothing runs it.
AGENT_IDS = tuple(
    [f"marketing.scout.{slug}" for slug in SCOUT_SLUGS]
    + [d[0] for i, d in enumerate(DOWNSTREAM) if i != 2]
)
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
    # This pipeline ENDS at qualification. Owner decision, Jon, 2026-08-12.
    #
    # It used to continue: qualifier_brief_composer -> gate_1_signals_inbox
    # (a blocking human_gate DMing Josh and Angela per signal) ->
    # content_brief_assembler (propose_initiation=True). Both are removed, and
    # they had to go together -- ``_execute_campaign_initiation_proposal``
    # requires an uninitiated candidate, and the only thing that creates one is
    # an operator SELECTION at Gate 1. Auto-approving the gate would therefore
    # have failed the assembler on every run rather than skipping it.
    #
    # Why: Jon does not want per-signal approval pings. "i dont want them to get
    # pinged about every signal that comes in thats what pops up in the campaign
    # signals slack channel" -- individual cards already land in
    # #campaign-signals, and the human touchpoint becomes Callie's combined
    # daily brief in #market-signals (see docs/market-signals-unification-note.md).
    # A daily brief cannot unblock a blocking gate, so the gate had to stop
    # blocking, not merely stop notifying.
    #
    # The failure this prevents, concretely: that gate sat suspended for 57 days
    # and silently blocked every later scheduled run, because its approvers were
    # configured as josh@ / angela@ -- addresses that do not exist in Slack. The
    # lookup missed, it fell back to an in-app queue nobody watched, and its
    # 72-hour escalation went to jon@, which also does not exist. Fixing those
    # addresses (done, same commit) only meant the unwanted design would start
    # delivering.
    #
    # Campaign initiation is not lost, it is deferred: it becomes a human-started
    # flow off the daily brief once that brief carries actions (Jon chose
    # "inform now, add actions later"). Both node definitions are recoverable
    # from this file's git history if that flow wants them back.
    scouts = [f"scout_{slug}" for slug in SCOUT_SLUGS]
    linear = [
        "qualifier_cross_reference",
        "qualifier_brief_composer",
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
