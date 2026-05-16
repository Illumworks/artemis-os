"""Marketing OS tools for Floating Artemis.

Authority layers:
  1: list_signals, get_signal, list_candidates, list_scout_runs,
     get_active_rulesets, list_content_assets
  2: qualify_signal, snooze_signal, fire_scout
  3: approve_signal, reject_signal, assemble_brief,
     submit_draft_for_review, decide_approval,
     propose_ruleset_change, link_content_asset

[surface:marketing-os] — all tools in this module are gated by the
marketing-os surface availability.
"""

from __future__ import annotations

import json
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

_SURFACE = "[surface:marketing-os]"


# ── Implementations ───────────────────────────────────────────────────────────


async def _list_signals(inp: dict[str, Any]) -> str:
    status = inp.get("status", "pending")
    limit = int(inp.get("limit", 20))
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            signals = await repo.list_signals(session, status=status, limit=limit)
        if not signals:
            return f"No signals with status='{status}'."
        lines = [f"{s.id}: [{s.status}] {s.headline or s.signal_type}" for s in signals]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_signals failed: {exc}"


async def _get_signal(inp: dict[str, Any]) -> str:
    signal_id = inp.get("signal_id")
    if not signal_id:
        return "Error: signal_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            signal = await repo.get_signal(session, int(signal_id))
        return json.dumps(
            {
                "id": signal.id,
                "status": signal.status,
                "headline": signal.headline,
                "signal_type": signal.signal_type,
                "score": signal.score,
            }
        )
    except Exception as exc:
        return f"get_signal failed: {exc}"


async def _qualify_signal(inp: dict[str, Any]) -> str:
    signal_id = inp.get("signal_id")
    score = inp.get("score")
    if not signal_id:
        return "Error: signal_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            await repo.qualify_signal(session, int(signal_id), score=score)
            await session.commit()
        return f"Signal {signal_id} qualified."
    except Exception as exc:
        return f"qualify_signal failed: {exc}"


async def _approve_signal(inp: dict[str, Any]) -> str:
    signal_id = inp.get("signal_id")
    if not signal_id:
        return "Error: signal_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            await repo.update_signal_status(session, int(signal_id), "approved")
            await session.commit()
        return f"Signal {signal_id} approved."
    except Exception as exc:
        return f"approve_signal failed: {exc}"


async def _reject_signal(inp: dict[str, Any]) -> str:
    signal_id = inp.get("signal_id")
    reason = inp.get("reason", "")
    if not signal_id:
        return "Error: signal_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            await repo.update_signal_status(session, int(signal_id), "rejected", reason=reason)
            await session.commit()
        return f"Signal {signal_id} rejected."
    except Exception as exc:
        return f"reject_signal failed: {exc}"


async def _snooze_signal(inp: dict[str, Any]) -> str:
    signal_id = inp.get("signal_id")
    until = inp.get("until")
    if not signal_id:
        return "Error: signal_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            await repo.update_signal_status(session, int(signal_id), "snoozed")
            await session.commit()
        msg = f"Signal {signal_id} snoozed"
        if until:
            msg += f" until {until}"
        return msg + "."
    except Exception as exc:
        return f"snooze_signal failed: {exc}"


async def _list_candidates(inp: dict[str, Any]) -> str:
    limit = int(inp.get("limit", 20))
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            candidates = await repo.list_candidates(session, limit=limit)
        if not candidates:
            return "No campaign candidates."
        lines = [f"{c.id}: {c.campaign_family or 'unknown'} — {c.status}" for c in candidates]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_candidates failed: {exc}"


async def _assemble_brief(inp: dict[str, Any]) -> str:
    candidate_id = inp.get("candidate_id")
    if not candidate_id:
        return "Error: candidate_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            brief = await repo.assemble_brief(session, int(candidate_id))
            await session.commit()
        return f"Brief assembled for candidate {candidate_id}: brief_id={brief.id}"
    except Exception as exc:
        return f"assemble_brief failed: {exc}"


async def _submit_draft_for_review(inp: dict[str, Any]) -> str:
    deliverable_id = inp.get("deliverable_id")
    if not deliverable_id:
        return "Error: deliverable_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            await repo.submit_deliverable(session, int(deliverable_id))
            await session.commit()
        return f"Deliverable {deliverable_id} submitted for review."
    except Exception as exc:
        return f"submit_draft_for_review failed: {exc}"


async def _decide_approval(inp: dict[str, Any]) -> str:
    approval_id = inp.get("approval_id")
    decision = inp.get("decision")
    if not approval_id or decision not in ("approve", "reject"):
        return "Error: approval_id and decision (approve|reject) are required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            await repo.record_approval_decision(session, int(approval_id), decision)
            await session.commit()
        return f"Approval {approval_id}: decision={decision}"
    except Exception as exc:
        return f"decide_approval failed: {exc}"


async def _list_scout_runs(inp: dict[str, Any]) -> str:
    limit = int(inp.get("limit", 20))
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            runs = await repo.list_scout_runs(session, limit=limit)
        if not runs:
            return "No scout runs."
        lines = [f"{r.id}: {r.scout_id} — {r.status} @ {r.started_at}" for r in runs]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_scout_runs failed: {exc}"


async def _fire_scout(inp: dict[str, Any]) -> str:
    scout_id = inp.get("scout_id")
    if not scout_id:
        return "Error: scout_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            run = await repo.create_scout_run(session, scout_id=scout_id)
            await session.commit()
        return f"Scout {scout_id} fired: run_id={run.id}"
    except Exception as exc:
        return f"fire_scout failed: {exc}"


async def _get_active_rulesets(inp: dict[str, Any]) -> str:  # noqa: ARG001
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            rulesets = await repo.list_rulesets(session, active_only=True)
        if not rulesets:
            return "No active rulesets."
        lines = [f"{r.id}: {r.campaign_family} v{r.version} — {r.status}" for r in rulesets]
        return "\n".join(lines)
    except Exception as exc:
        return f"get_active_rulesets failed: {exc}"


async def _propose_ruleset_change(inp: dict[str, Any]) -> str:
    ruleset_id = inp.get("ruleset_id")
    changes = inp.get("changes", {})
    if not ruleset_id:
        return "Error: ruleset_id is required"
    proposal = {
        "type": "ruleset_change_proposal",
        "ruleset_id": ruleset_id,
        "changes": changes,
    }
    return f"Ruleset change proposal (pending confirmation):\n{json.dumps(proposal, indent=2)}"


async def _list_content_assets(inp: dict[str, Any]) -> str:
    limit = int(inp.get("limit", 20))
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            assets = await repo.list_content_assets(session, limit=limit)
        if not assets:
            return "No content assets."
        lines = [f"{a.id}: {a.asset_type} — {a.title or '(untitled)'}" for a in assets]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_content_assets failed: {exc}"


async def _link_content_asset(inp: dict[str, Any]) -> str:
    candidate_id = inp.get("candidate_id")
    asset_id = inp.get("asset_id")
    role = inp.get("role", "reference")
    if not candidate_id or not asset_id:
        return "Error: candidate_id and asset_id are required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            await repo.link_content_asset(
                session,
                candidate_id=int(candidate_id),
                asset_id=int(asset_id),
                role=role,
            )
            await session.commit()
        return f"Asset {asset_id} linked to candidate {candidate_id} with role={role}."
    except Exception as exc:
        return f"link_content_asset failed: {exc}"


# ── Tool definitions ──────────────────────────────────────────────────────────

_s = _SURFACE  # shorthand

LIST_SIGNALS = Tool(
    name="list_signals",
    description=f"List marketing signals from the signal queue. {_s} [layer:1]",
    input_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string", "default": "pending"},
            "limit": {"type": "integer", "default": 20},
        },
        "required": [],
    },
)

GET_SIGNAL = Tool(
    name="get_signal",
    description=f"Get a single marketing signal by ID. {_s} [layer:1]",
    input_schema={
        "type": "object",
        "properties": {"signal_id": {"type": "integer"}},
        "required": ["signal_id"],
    },
)

QUALIFY_SIGNAL = Tool(
    name="qualify_signal",
    description=f"Mark a signal as qualified (idempotent score update). {_s} [layer:2]",
    input_schema={
        "type": "object",
        "properties": {
            "signal_id": {"type": "integer"},
            "score": {"type": "number"},
        },
        "required": ["signal_id"],
    },
)

APPROVE_SIGNAL = Tool(
    name="approve_signal",
    description=f"Approve a signal (side-effect: status change + downstream triggers). {_s} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {"signal_id": {"type": "integer"}},
        "required": ["signal_id"],
    },
)

REJECT_SIGNAL = Tool(
    name="reject_signal",
    description=f"Reject a signal. {_s} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "signal_id": {"type": "integer"},
            "reason": {"type": "string"},
        },
        "required": ["signal_id"],
    },
)

SNOOZE_SIGNAL = Tool(
    name="snooze_signal",
    description=f"Snooze a signal until a later time. {_s} [layer:2]",
    input_schema={
        "type": "object",
        "properties": {
            "signal_id": {"type": "integer"},
            "until": {"type": "string", "description": "ISO datetime or description"},
        },
        "required": ["signal_id"],
    },
)

LIST_CANDIDATES = Tool(
    name="list_candidates",
    description=f"List campaign candidates. {_s} [layer:1]",
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 20}},
        "required": [],
    },
)

ASSEMBLE_BRIEF = Tool(
    name="assemble_brief",
    description=f"Assemble a campaign brief for a candidate. {_s} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {"candidate_id": {"type": "integer"}},
        "required": ["candidate_id"],
    },
)

SUBMIT_DRAFT_FOR_REVIEW = Tool(
    name="submit_draft_for_review",
    description=f"Submit a draft deliverable for review. {_s} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {"deliverable_id": {"type": "integer"}},
        "required": ["deliverable_id"],
    },
)

DECIDE_APPROVAL = Tool(
    name="decide_approval",
    description=f"Record an approve or reject decision for an approval gate. {_s} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "approval_id": {"type": "integer"},
            "decision": {"type": "string", "enum": ["approve", "reject"]},
        },
        "required": ["approval_id", "decision"],
    },
)

LIST_SCOUT_RUNS = Tool(
    name="list_scout_runs",
    description=f"List recent scout runs. {_s} [layer:1]",
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 20}},
        "required": [],
    },
)

FIRE_SCOUT = Tool(
    name="fire_scout",
    description=f"Trigger a scout run immediately. {_s} [layer:2]",
    input_schema={
        "type": "object",
        "properties": {"scout_id": {"type": "string"}},
        "required": ["scout_id"],
    },
)

GET_ACTIVE_RULESETS = Tool(
    name="get_active_rulesets",
    description=f"Get current active signal qualification rulesets. {_s} [layer:1]",
    input_schema={"type": "object", "properties": {}, "required": []},
)

PROPOSE_RULESET_CHANGE = Tool(
    name="propose_ruleset_change",
    description=f"Propose a change to a qualification ruleset. {_s} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "ruleset_id": {"type": "integer"},
            "changes": {"type": "object"},
        },
        "required": ["ruleset_id", "changes"],
    },
)

LIST_CONTENT_ASSETS = Tool(
    name="list_content_assets",
    description=f"List content assets in the library. {_s} [layer:1]",
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 20}},
        "required": [],
    },
)

LINK_CONTENT_ASSET = Tool(
    name="link_content_asset",
    description=f"Link a content asset to a campaign candidate. {_s} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "candidate_id": {"type": "integer"},
            "asset_id": {"type": "integer"},
            "role": {"type": "string", "default": "reference"},
        },
        "required": ["candidate_id", "asset_id"],
    },
)


def register_marketing_tools(registry: AuthorizedToolRegistry) -> None:
    """Register all marketing tools into the provided registry."""
    registry.register(LIST_SIGNALS, _list_signals, layer=1)
    registry.register(GET_SIGNAL, _get_signal, layer=1)
    registry.register(QUALIFY_SIGNAL, _qualify_signal, layer=2)
    registry.register(APPROVE_SIGNAL, _approve_signal, layer=3)
    registry.register(REJECT_SIGNAL, _reject_signal, layer=3)
    registry.register(SNOOZE_SIGNAL, _snooze_signal, layer=2)
    registry.register(LIST_CANDIDATES, _list_candidates, layer=1)
    registry.register(ASSEMBLE_BRIEF, _assemble_brief, layer=3)
    registry.register(SUBMIT_DRAFT_FOR_REVIEW, _submit_draft_for_review, layer=3)
    registry.register(DECIDE_APPROVAL, _decide_approval, layer=3)
    registry.register(LIST_SCOUT_RUNS, _list_scout_runs, layer=1)
    registry.register(FIRE_SCOUT, _fire_scout, layer=2)
    registry.register(GET_ACTIVE_RULESETS, _get_active_rulesets, layer=1)
    registry.register(PROPOSE_RULESET_CHANGE, _propose_ruleset_change, layer=3)
    registry.register(LIST_CONTENT_ASSETS, _list_content_assets, layer=1)
    registry.register(LINK_CONTENT_ASSET, _link_content_asset, layer=3)
