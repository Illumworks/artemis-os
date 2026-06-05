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
from artemis.floating_artemis.context import floating_session_id_var

_SURFACE = "[surface:marketing-os]"


# ── Implementations ───────────────────────────────────────────────────────────


async def _list_signals(inp: dict[str, Any]) -> str:
    status = inp.get("status", "pending_qualification")
    limit = int(inp.get("limit", 20))
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            signals = await repo.list_signals(session, status=status, limit=limit)
        if not signals:
            return f"No signals with status='{status}'."
        lines = [f"{s.id}: [{s.signal_status}] {s.headline}" for s in signals]
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
                "signal_status": signal.signal_status,
                "headline": signal.headline,
                "campaign_family": signal.campaign_family,
            }
        )
    except Exception as exc:
        return f"get_signal failed: {exc}"


async def _qualify_signal(inp: dict[str, Any]) -> str:
    signal_id = inp.get("signal_id")
    qualification = inp.get("qualification")
    if qualification is None and "score" in inp:
        qualification = {"fitScore": inp.get("score")}
    if qualification is None:
        qualification = {}
    if not signal_id:
        return "Error: signal_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            await repo.save_signal_qualification(session, int(signal_id), qualification)
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
            await repo.update_signal(session, int(signal_id), signal_status="approved")
            await session.commit()

        # MC5: fire-and-forget memory carryover (failure must not break approval)
        import asyncio as _asyncio

        from artemis.builder.memory_carryover import write_fa_marketing_approval_observation

        fa_session_id = str(
            inp.get("session_id")
            or inp.get("fa_session_id")
            or floating_session_id_var.get()
            or "unknown"
        )
        user_directive = str(inp.get("directive") or inp.get("user_directive") or "")
        _asyncio.create_task(
            write_fa_marketing_approval_observation(
                signal_id=int(signal_id),
                new_status="approved",
                fa_session_id=fa_session_id,
                user_directive=user_directive or None,
            )
        )

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
            await repo.update_signal(
                session, int(signal_id), signal_status="rejected", rejected_reason=reason
            )
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
            await repo.update_signal(
                session, int(signal_id), signal_status="snoozed", snoozed_until=until
            )
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
        lines = [
            f"{c.id}: {c.campaign_family or 'unknown'} — {c.decision_state}" for c in candidates
        ]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_candidates failed: {exc}"


async def _assemble_brief(inp: dict[str, Any]) -> str:
    candidate_id = inp.get("candidate_id")
    brief_content = inp.get("content", {})
    if not candidate_id:
        return "Error: candidate_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            brief = await repo.create_campaign_brief(
                session, candidate_id=int(candidate_id), content=brief_content
            )
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
        from artemis.marketing.writing_studio import invoke as ws_invoke

        async with _db.SessionLocal() as session:
            approval = await ws_invoke.submit_draft_for_review(session, int(deliverable_id))
        return f"Deliverable {deliverable_id} submitted for review: approval_id={approval.id}"
    except Exception as exc:
        return f"submit_draft_for_review failed: {exc}"


async def _decide_approval(inp: dict[str, Any]) -> str:
    approval_id = inp.get("approval_id")
    decision = inp.get("decision")
    decided_by = inp.get("decided_by", "artemis")
    if not approval_id or decision not in ("approve", "reject"):
        return "Error: approval_id and decision (approve|reject) are required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            await repo.decide_approval(
                session, int(approval_id), decision=decision, decided_by=decided_by
            )
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
        lines = [f"{r.id}: {r.scout_type} — {r.status} @ {r.started_at}" for r in runs]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_scout_runs failed: {exc}"


async def _fire_scout(inp: dict[str, Any]) -> str:
    scout_type = inp.get("scout_type") or inp.get("scout_id")
    if not scout_type:
        return "Error: scout_type is required"
    try:
        import uuid

        import artemis.db as _db
        from artemis.marketing import repository as repo

        run_id = f"scout_run_{uuid.uuid4().hex[:8]}"
        async with _db.SessionLocal() as session:
            run = await repo.create_scout_run(session, run_id=run_id, scout_type=scout_type)
            await session.commit()
        return f"Scout {scout_type} fired: run_id={run.id}"
    except Exception as exc:
        return f"fire_scout failed: {exc}"


async def _get_active_rulesets(inp: dict[str, Any]) -> str:  # noqa: ARG001
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            rulesets = await repo.list_ruleset_versions(session)
        if not rulesets:
            return "No rulesets."
        lines = [f"{r.id}: {r.family} v{r.version_tag}" for r in rulesets]
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
    try:
        from sqlalchemy import select

        import artemis.db as _db
        from artemis.marketing import repository as repo
        from artemis.marketing.models import Ruleset

        async with _db.SessionLocal() as session:
            existing = await session.execute(
                select(Ruleset.id).where(Ruleset.id == int(ruleset_id))
            )
            if existing.scalar_one_or_none() is None:
                return f"Error: ruleset_id {ruleset_id} not found"
            row = await repo.create_approval(
                session,
                kind="ruleset_change",
                subject_id=str(ruleset_id),
                decision_payload=proposal,
            )
            await session.commit()
        return (
            f"Ruleset change proposal saved: approval_id={row.id}\n{json.dumps(proposal, indent=2)}"
        )
    except Exception as exc:
        return f"propose_ruleset_change failed: {exc}"


async def _list_content_assets(inp: dict[str, Any]) -> str:
    limit = int(inp.get("limit", 20))
    status = inp.get("status")
    asset_type = inp.get("asset_type") or inp.get("assetType")
    campaign_family = inp.get("campaign_family")
    try:
        from sqlalchemy import select

        import artemis.db as _db
        from artemis.marketing.models import ContentAsset

        async with _db.SessionLocal() as session:
            stmt = select(ContentAsset)
            if status:
                stmt = stmt.where(ContentAsset.status == status)
            if asset_type:
                stmt = stmt.where(ContentAsset.asset_type == asset_type)
            if campaign_family:
                stmt = stmt.where(
                    ContentAsset.asset_metadata["campaign_family"].as_string()
                    == str(campaign_family)
                )
            stmt = stmt.order_by(ContentAsset.id.desc()).limit(limit)
            result = await session.execute(stmt)
            assets = list(result.scalars().all())
        if not assets:
            return "No content assets found."
        lines = [
            f"{asset.id}: [{asset.status}] {asset.asset_type} — {asset.summary or '(no summary)'}"
            for asset in assets
        ]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_content_assets failed: {exc}"


async def _link_content_asset(inp: dict[str, Any]) -> str:
    candidate_id = inp.get("candidate_id")
    asset_id = inp.get("asset_id")
    if not candidate_id or not asset_id:
        return "Error: candidate_id and asset_id are required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            await repo.link_content_asset_to_candidate(
                session,
                candidate_id=int(candidate_id),
                asset_id=int(asset_id),
                link_role=str(inp.get("role")) if inp.get("role") is not None else None,
            )
            await session.commit()
        return f"Asset {asset_id} linked to candidate {candidate_id}."
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
            "status": {"type": "string", "default": "pending_qualification"},
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
            "qualification": {"type": "object", "default": {}},
            "score": {
                "type": "number",
                "description": "Legacy shorthand; stored as qualification.fitScore when qualification is omitted.",
            },
        },
        "required": ["signal_id"],
    },
)

APPROVE_SIGNAL = Tool(
    name="approve_signal",
    description=f"Approve a signal (side-effect: status change + downstream triggers). {_s} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "signal_id": {"type": "integer"},
            "fa_session_id": {"type": "string"},
            "directive": {"type": "string"},
        },
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
        "properties": {
            "candidate_id": {"type": "integer"},
            "content": {"type": "object", "default": {}},
        },
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
            "decided_by": {"type": "string", "default": "artemis"},
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
        "properties": {
            "scout_type": {"type": "string"},
            "scout_id": {
                "type": "string",
                "description": "Legacy alias for scout_type.",
            },
        },
        "required": [],
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
        "properties": {
            "limit": {"type": "integer", "default": 20},
            "status": {"type": "string"},
            "asset_type": {"type": "string"},
            "campaign_family": {"type": "string"},
        },
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
