"""Natural pending-reply routing for Slack DM conversations.

This module gives Artemis one shared view of "what is currently awaiting Jon"
so a reply like "go ahead with the Slack one" can be interpreted against the
full pending picture instead of independent yes/no flows racing each other.

Safety posture:
- This router NEVER executes raw side effects itself.
- Proposal approvals still go through proposed -> approved -> executed.
- Any ambiguity resolves to clarify or normal conversation, never assume-yes.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.client import CompletionRequest, ModelAdapter
from artemis.agent.types import Message, TextBlock
from artemis.floating_artemis.repository import list_messages_for_context
from artemis.okr import repository as okr_repo
from artemis.proactivity.agency_gate import _run_approved_action
from artemis.proactivity.models import Commitment, ProposedAction, RadarSurfacedItem
from artemis.proactivity.proposed_actions_repository import (
    approve_proposed_action,
    expire_stale_proposals,
    list_pending_for_user,
    reject_proposed_action,
)
from artemis.proactivity.repository import (
    clear_staged_updates,
    complete_okr_checkin_breadcrumb,
    get_live_okr_checkin_breadcrumb,
)
from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter

logger = logging.getLogger(__name__)

ConfirmClassifier = Callable[[str], Awaitable[str]]

_ACTION_CONFIDENCE_THRESHOLD = 0.86
_MIXED_DOMAIN_CONFIDENCE_THRESHOLD = 0.94
_RECENT_MESSAGE_LIMIT = 6
_COMMITMENT_LIMIT = 5
_RADAR_LIMIT = 5

_ID_TAG_RE = re.compile(r"\bA(\d+)\b", re.IGNORECASE)
_OKR_MARKER_RE = re.compile(r"\b(?:okr|krs?|key result|check[- ]?in|progress)\b", re.IGNORECASE)
_ACTIONY_RE = re.compile(
    r"\b(?:yes|no|go|approve|approved|reject|rejected|skip|cancel|hold|do both|both|send|ship)\b",
    re.IGNORECASE,
)
_AFFIRM_RE = re.compile(
    r"\b(?:yes|go|approve|approved|do it|go ahead|ship it|send it|both)\b",
    re.IGNORECASE,
)
_REJECT_RE = re.compile(
    r"\b(?:no|reject|rejected|skip|cancel|hold off|don't|dont|not now)\b",
    re.IGNORECASE,
)

_ROUTER_SYSTEM = """You decide ONE thing: is this Slack reply the operator ACTING ON a
pending proposal/approval, or is it normal conversation? Default hard to conversation.

Return JSON only. No markdown, no prose outside JSON.

Allowed intents:
- approve_proposals
- reject_proposals
- apply_okr_updates
- reject_okr_updates
- clarify
- converse

DEFAULT TO "converse". Only pick an action or "clarify" intent when the reply is
UNMISTAKABLY trying to act on a specific pending item.

Choose "converse" (hand off to normal conversation) whenever the reply is any of:
- a QUESTION (e.g. "why are you giving me a daily brief?", "what's pending?")
- a NEW instruction/request/preference ("don't do morning briefs on weekends",
  "stop X", "change Y", "can you confirm ...")
- a topic change, small talk, or anything not clearly approving/selecting a pending item
- unclear whether it even refers to the pending items at all
When in any doubt, choose "converse". Hijacking a real message (a question or an
instruction) is far worse than missing an approval — the operator can just restate it.

Choose approve_proposals / reject_proposals / apply_okr_updates / reject_okr_updates
ONLY on an explicit, high-confidence reference to a specific pending item
(e.g. "yes, send the CNN invite", "approve 2", "no, skip the podcast one").

Choose "clarify" ONLY when the reply is clearly trying to approve/select a pending
item but is genuinely ambiguous about WHICH one — NEVER for a question or a new topic.

- Never infer approval from vague positivity.
- If approving/rejecting proposals, include the exact proposal_ids.
- If clarifying, provide one short natural question in reply_text.

JSON schema:
{
  "intent": "approve_proposals" | "reject_proposals" | "apply_okr_updates" | "reject_okr_updates" | "clarify" | "converse",
  "proposal_ids": [number],
  "confidence": 0.0,
  "reply_text": "short natural response or clarifying question",
  "reason": "brief explanation"
}
"""


@dataclass(frozen=True)
class PendingProposalDigest:
    id: int
    action_type: str
    preview: str
    short_label: str


@dataclass(frozen=True)
class PendingOkrDigest:
    kr_id: int
    kr_title: str
    objective_title: str
    progress: int
    basis: str
    bullet: str | None = None


@dataclass(frozen=True)
class RadarDigest:
    id: int
    label: str
    permalink: str | None


@dataclass(frozen=True)
class CommitmentDigest:
    id: int
    text: str
    status: str


@dataclass(frozen=True)
class PendingContext:
    proposals: list[PendingProposalDigest]
    staged_okr_updates: list[PendingOkrDigest]
    open_commitments: list[CommitmentDigest]
    recent_radar_items: list[RadarDigest]
    recent_messages: list[str]

    @property
    def has_actionables(self) -> bool:
        return bool(self.proposals or self.staged_okr_updates)

    @property
    def has_mixed_domains(self) -> bool:
        return bool(self.proposals and self.staged_okr_updates)


@dataclass(frozen=True)
class PendingDecision:
    intent: str
    proposal_ids: list[int]
    confidence: float
    reply_text: str | None
    reason: str

    @property
    def is_actioning(self) -> bool:
        return self.intent in {
            "approve_proposals",
            "reject_proposals",
            "apply_okr_updates",
            "reject_okr_updates",
        }


@dataclass(frozen=True)
class PendingReplyOutcome:
    handled: bool
    intent: str
    outbound_text: str | None
    confidence: float


async def assemble_pending_context(
    session: AsyncSession,
    *,
    slack_user_id: str,
    session_id: str | None = None,
    now: datetime | None = None,
) -> PendingContext:
    """Return a unified structured view of what is currently pending."""
    current = now or datetime.now(UTC)
    await expire_stale_proposals(session, now=current)

    proposal_rows = await list_pending_for_user(session, target_user_id=slack_user_id, now=current)
    proposals = [
        PendingProposalDigest(
            id=row.id,
            action_type=row.action_type,
            preview=row.preview,
            short_label=_proposal_short_label(row),
        )
        for row in proposal_rows
    ]

    crumb = await get_live_okr_checkin_breadcrumb(session, slack_user_id)
    staged_okr_updates: list[PendingOkrDigest] = []
    if crumb is not None and crumb.staged_updates:
        snapshot_by_kr_id: dict[int, dict[str, Any]] = {}
        for raw in list(crumb.kr_snapshot or []):
            if isinstance(raw, dict):
                try:
                    raw_kr_id = raw.get("kr_id")
                    if raw_kr_id is None:
                        continue
                    snapshot_by_kr_id[int(raw_kr_id)] = raw
                except (TypeError, ValueError):
                    continue
        for raw in list(crumb.staged_updates or []):
            if not isinstance(raw, dict):
                continue
            try:
                raw_kr_id = raw.get("kr_id")
                raw_progress = raw.get("progress")
                if raw_kr_id is None or raw_progress is None:
                    continue
                kr_id = int(raw_kr_id)
                progress = int(raw_progress)
            except (TypeError, ValueError):
                continue
            snap = snapshot_by_kr_id.get(kr_id, {})
            staged_okr_updates.append(
                PendingOkrDigest(
                    kr_id=kr_id,
                    kr_title=str(snap.get("kr_title") or f"KR {kr_id}"),
                    objective_title=str(snap.get("objective_title") or ""),
                    progress=progress,
                    basis=str(raw.get("basis") or ""),
                    bullet=str(raw.get("bullet") or "").strip() or None,
                )
            )

    commitments_result = await session.execute(
        select(Commitment)
        .where(Commitment.status.in_(("active", "snoozed")))
        .order_by(
            Commitment.due.asc().nulls_last(), Commitment.created_at.asc(), Commitment.id.asc()
        )
        .limit(_COMMITMENT_LIMIT)
    )
    open_commitments = [
        CommitmentDigest(id=row.id, text=row.text, status=row.status)
        for row in commitments_result.scalars().all()
    ]

    radar_result = await session.execute(
        select(RadarSurfacedItem)
        .where(RadarSurfacedItem.dismissed_at.is_(None))
        .order_by(desc(RadarSurfacedItem.last_surfaced_at), desc(RadarSurfacedItem.id))
        .limit(_RADAR_LIMIT)
    )
    recent_radar_items = [
        RadarDigest(id=row.id, label=row.label, permalink=row.permalink)
        for row in radar_result.scalars().all()
    ]

    recent_messages: list[str] = []
    if session_id:
        history_rows = await list_messages_for_context(
            session,
            session_id,
            limit=_RECENT_MESSAGE_LIMIT,
            created_at_gte=current - timedelta(days=3),
        )
        for row in history_rows:
            flattened = _flatten_message_content(row.role, row.content)
            if flattened:
                recent_messages.append(flattened)

    return PendingContext(
        proposals=proposals,
        staged_okr_updates=staged_okr_updates,
        open_commitments=open_commitments,
        recent_radar_items=recent_radar_items,
        recent_messages=recent_messages,
    )


async def route_pending_reply(
    session: AsyncSession,
    *,
    session_id: str,
    slack_user_id: str,
    text: str,
    adapter: ModelAdapter | None = None,
    confirm_classifier: ConfirmClassifier | None = None,
    now: datetime | None = None,
) -> PendingReplyOutcome:
    """Interpret *text* against unified pending context and execute safely."""
    current = now or datetime.now(UTC)
    context = await assemble_pending_context(
        session,
        slack_user_id=slack_user_id,
        session_id=session_id,
        now=current,
    )
    if not context.has_actionables:
        return PendingReplyOutcome(
            handled=False,
            intent="converse",
            outbound_text=None,
            confidence=0.0,
        )

    decision = await _decide_pending_reply(
        session=session,
        session_id=session_id,
        context=context,
        text=text,
        adapter=adapter,
        confirm_classifier=confirm_classifier,
    )
    logger.info(
        "natural_pending_router: session=%s user=%s intent=%s confidence=%.2f proposals=%s staged_okr=%s reason=%s",
        session_id,
        slack_user_id,
        decision.intent,
        decision.confidence,
        decision.proposal_ids,
        bool(context.staged_okr_updates),
        decision.reason,
    )

    if decision.intent == "converse":
        return PendingReplyOutcome(
            handled=False,
            intent=decision.intent,
            outbound_text=None,
            confidence=decision.confidence,
        )

    if decision.intent == "clarify":
        return PendingReplyOutcome(
            handled=True,
            intent=decision.intent,
            outbound_text=decision.reply_text or _default_clarification(context),
            confidence=decision.confidence,
        )

    # Defense-in-depth (Lead hardening): intents that SEND / CREATE / CHANGE must
    # clear a confidence floor enforced in CODE, not just asked of the LLM prompt.
    # Below it we ASK rather than act — never assume-yes. Reject/converse/clarify
    # are safe (they cancel or do nothing) and are exempt. Clean single-item yes/no
    # comes through the confirm-classifier at confidence 1.0, so it is unaffected.
    if decision.intent in ("approve_proposals", "apply_okr_updates") and decision.confidence < 0.7:
        logger.info(
            "natural_pending_router: downgrading %s (confidence=%.2f < floor) to clarify",
            decision.intent,
            decision.confidence,
        )
        return PendingReplyOutcome(
            handled=True,
            intent="clarify",
            outbound_text=decision.reply_text or _default_clarification(context),
            confidence=decision.confidence,
        )

    if decision.intent == "approve_proposals":
        results = await _approve_proposals(
            session,
            action_ids=decision.proposal_ids,
            actor=slack_user_id,
            now=current,
        )
        if any(not _is_success_result(line) for line in results):
            outbound_text = " ".join(results)
        else:
            outbound_text = decision.reply_text or _default_action_reply(context, decision)
        return PendingReplyOutcome(
            handled=True,
            intent=decision.intent,
            outbound_text=outbound_text,
            confidence=decision.confidence,
        )

    if decision.intent == "reject_proposals":
        results = await _reject_proposals(
            session,
            action_ids=decision.proposal_ids,
            actor=slack_user_id,
            now=current,
        )
        if any(not _is_success_result(line) for line in results):
            outbound_text = " ".join(results)
        else:
            outbound_text = decision.reply_text or _default_action_reply(context, decision)
        return PendingReplyOutcome(
            handled=True,
            intent=decision.intent,
            outbound_text=outbound_text,
            confidence=decision.confidence,
        )

    if decision.intent == "apply_okr_updates":
        okr_text = await _apply_staged_okr_updates(session, slack_user_id=slack_user_id)
        if _is_success_result(okr_text):
            okr_text = decision.reply_text or _default_action_reply(context, decision)
        return PendingReplyOutcome(
            handled=True,
            intent=decision.intent,
            outbound_text=okr_text,
            confidence=decision.confidence,
        )

    if decision.intent == "reject_okr_updates":
        okr_text = await _discard_staged_okr_updates(session, slack_user_id=slack_user_id)
        if _is_success_result(okr_text):
            okr_text = decision.reply_text or _default_action_reply(context, decision)
        return PendingReplyOutcome(
            handled=True,
            intent=decision.intent,
            outbound_text=okr_text,
            confidence=decision.confidence,
        )

    return PendingReplyOutcome(
        handled=False,
        intent="converse",
        outbound_text=None,
        confidence=decision.confidence,
    )


async def _decide_pending_reply(
    *,
    session: AsyncSession,
    session_id: str,
    context: PendingContext,
    text: str,
    adapter: ModelAdapter | None,
    confirm_classifier: ConfirmClassifier | None,
) -> PendingDecision:
    if confirm_classifier is not None:
        try:
            verdict = await confirm_classifier(text)
            if verdict in {"YES", "NO"}:
                is_yes = verdict == "YES"
                if context.staged_okr_updates and not context.proposals:
                    return PendingDecision(
                        intent="apply_okr_updates" if is_yes else "reject_okr_updates",
                        proposal_ids=[],
                        confidence=1.0,
                        reply_text=None,
                        reason=f"confirm_classifier:{verdict.lower()}",
                    )
                if len(context.proposals) == 1 and not context.staged_okr_updates:
                    return PendingDecision(
                        intent="approve_proposals" if is_yes else "reject_proposals",
                        proposal_ids=[context.proposals[0].id],
                        confidence=1.0,
                        reply_text=None,
                        reason=f"confirm_classifier:{verdict.lower()}",
                    )
            if verdict == "NEITHER" and context.staged_okr_updates and not context.proposals:
                return PendingDecision(
                    intent="converse",
                    proposal_ids=[],
                    confidence=1.0,
                    reply_text=None,
                    reason="confirm_classifier:neither",
                )
        except Exception:
            logger.debug(
                "natural_pending_router: confirm classifier fallback failed", exc_info=True
            )

    if adapter is None:
        try:
            adapter = resolve_adapter(provider="claude-code")
        except NoProviderAvailableError:
            adapter = None

    if adapter is not None:
        try:
            prompt = _build_router_prompt(session_id=session_id, context=context, text=text)
            response = await adapter.complete(
                CompletionRequest(
                    messages=[Message(role="user", content=[TextBlock(text=prompt)])],
                    system=_ROUTER_SYSTEM,
                    max_tokens=450,
                    reasoning_effort="low",
                    cache_system=False,
                    cache_tools=False,
                )
            )
            decision = _parse_router_decision(_flatten_blocks(response.message.content))
            validated = _validate_decision(decision, context, text)
            if validated is not None:
                return validated
        except Exception:
            logger.warning("natural_pending_router: LLM routing failed", exc_info=True)

    fallback = _deterministic_fallback(context, text)
    if fallback is not None:
        return fallback

    if _ACTIONY_RE.search(text):
        return PendingDecision(
            intent="clarify",
            proposal_ids=[],
            confidence=0.4,
            reply_text=_default_clarification(context),
            reason="pending_action_reply_without_safe_match",
        )

    return PendingDecision(
        intent="converse",
        proposal_ids=[],
        confidence=0.2,
        reply_text=None,
        reason="no_safe_action_match",
    )


def _build_router_prompt(*, session_id: str, context: PendingContext, text: str) -> str:
    payload = {
        "session_id": session_id,
        "recent_messages": context.recent_messages,
        "pending_proposals": [asdict(item) for item in context.proposals],
        "staged_okr_updates": [asdict(item) for item in context.staged_okr_updates],
        "open_commitments": [asdict(item) for item in context.open_commitments],
        "recent_radar_items": [asdict(item) for item in context.recent_radar_items],
        "operator_reply": text,
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _parse_router_decision(raw_text: str) -> PendingDecision:
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"Router returned no JSON object: {raw_text[:200]!r}")
    payload = json.loads(raw_text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Router payload is not an object")
    proposal_ids_raw = payload.get("proposal_ids") or []
    proposal_ids = [
        int(item)
        for item in proposal_ids_raw
        if isinstance(item, int | float | str) and str(item).strip()
    ]
    return PendingDecision(
        intent=str(payload.get("intent") or "clarify"),
        proposal_ids=list(dict.fromkeys(proposal_ids)),
        confidence=float(payload.get("confidence") or 0.0),
        reply_text=str(payload.get("reply_text") or "").strip() or None,
        reason=str(payload.get("reason") or "").strip(),
    )


def _validate_decision(
    decision: PendingDecision,
    context: PendingContext,
    text: str,
) -> PendingDecision | None:
    valid_intents = {
        "approve_proposals",
        "reject_proposals",
        "apply_okr_updates",
        "reject_okr_updates",
        "clarify",
        "converse",
    }
    if decision.intent not in valid_intents:
        return None

    if decision.intent in {"clarify", "converse"}:
        return decision

    threshold = (
        _MIXED_DOMAIN_CONFIDENCE_THRESHOLD
        if context.has_mixed_domains and decision.is_actioning
        else _ACTION_CONFIDENCE_THRESHOLD
    )
    if decision.confidence < threshold:
        return PendingDecision(
            intent="clarify",
            proposal_ids=[],
            confidence=decision.confidence,
            reply_text=decision.reply_text or _default_clarification(context),
            reason=f"low_confidence:{decision.confidence:.2f}",
        )

    proposal_ids = {item.id for item in context.proposals}
    if decision.intent in {"approve_proposals", "reject_proposals"}:
        if not decision.proposal_ids:
            return PendingDecision(
                intent="clarify",
                proposal_ids=[],
                confidence=decision.confidence,
                reply_text=_default_clarification(context),
                reason="proposal_action_missing_ids",
            )
        if any(pid not in proposal_ids for pid in decision.proposal_ids):
            return PendingDecision(
                intent="clarify",
                proposal_ids=[],
                confidence=decision.confidence,
                reply_text=_default_clarification(context),
                reason="proposal_action_unknown_id",
            )
        if context.has_mixed_domains and not _has_explicit_domain_reference(
            text, context, decision
        ):
            return PendingDecision(
                intent="clarify",
                proposal_ids=[],
                confidence=decision.confidence,
                reply_text=_default_clarification(context),
                reason="mixed_domain_without_explicit_reference",
            )
        return decision

    if decision.intent in {"apply_okr_updates", "reject_okr_updates"}:
        if not context.staged_okr_updates:
            return PendingDecision(
                intent="clarify",
                proposal_ids=[],
                confidence=decision.confidence,
                reply_text=_default_clarification(context),
                reason="okr_action_without_staged_updates",
            )
        if context.has_mixed_domains and not _has_explicit_domain_reference(
            text, context, decision
        ):
            return PendingDecision(
                intent="clarify",
                proposal_ids=[],
                confidence=decision.confidence,
                reply_text=_default_clarification(context),
                reason="mixed_domain_without_explicit_reference",
            )
        return decision

    return None


def _deterministic_fallback(context: PendingContext, text: str) -> PendingDecision | None:
    normalized = " ".join(text.lower().split())
    mentioned_ids = [int(item) for item in _ID_TAG_RE.findall(text)]
    proposal_ids = {item.id for item in context.proposals}
    if mentioned_ids and all(item in proposal_ids for item in mentioned_ids):
        if _REJECT_RE.search(normalized):
            return PendingDecision(
                intent="reject_proposals",
                proposal_ids=mentioned_ids,
                confidence=0.99,
                reply_text=None,
                reason="explicit_proposal_ids_reject",
            )
        if _AFFIRM_RE.search(normalized):
            return PendingDecision(
                intent="approve_proposals",
                proposal_ids=mentioned_ids,
                confidence=0.99,
                reply_text=None,
                reason="explicit_proposal_ids_approve",
            )

    if context.has_mixed_domains and _OKR_MARKER_RE.search(normalized):
        if _REJECT_RE.search(normalized):
            return PendingDecision(
                intent="reject_okr_updates",
                proposal_ids=[],
                confidence=0.93,
                reply_text=None,
                reason="explicit_okr_reference_reject",
            )
        if _AFFIRM_RE.search(normalized):
            return PendingDecision(
                intent="apply_okr_updates",
                proposal_ids=[],
                confidence=0.93,
                reply_text=None,
                reason="explicit_okr_reference_apply",
            )

    if context.proposals:
        if any("slack" in p.action_type for p in context.proposals) and "slack" in normalized:
            matched = [p.id for p in context.proposals if "slack" in p.action_type]
            if matched:
                intent = (
                    "reject_proposals" if _REJECT_RE.search(normalized) else "approve_proposals"
                )
                if _AFFIRM_RE.search(normalized) or _REJECT_RE.search(normalized):
                    return PendingDecision(
                        intent=intent,
                        proposal_ids=matched,
                        confidence=0.91,
                        reply_text=None,
                        reason="proposal_domain_keyword_slack",
                    )
        if any("gmail" in p.action_type for p in context.proposals) and (
            "email" in normalized or "gmail" in normalized
        ):
            matched = [p.id for p in context.proposals if "gmail" in p.action_type]
            if matched:
                intent = (
                    "reject_proposals" if _REJECT_RE.search(normalized) else "approve_proposals"
                )
                if _AFFIRM_RE.search(normalized) or _REJECT_RE.search(normalized):
                    return PendingDecision(
                        intent=intent,
                        proposal_ids=matched,
                        confidence=0.91,
                        reply_text=None,
                        reason="proposal_domain_keyword_email",
                    )
        if not context.staged_okr_updates and len(context.proposals) > 1:
            if _AFFIRM_RE.search(normalized) and "both" in normalized:
                return PendingDecision(
                    intent="approve_proposals",
                    proposal_ids=[item.id for item in context.proposals],
                    confidence=0.9,
                    reply_text=None,
                    reason="approve_both_proposals",
                )
            if _REJECT_RE.search(normalized) and "both" in normalized:
                return PendingDecision(
                    intent="reject_proposals",
                    proposal_ids=[item.id for item in context.proposals],
                    confidence=0.9,
                    reply_text=None,
                    reason="reject_both_proposals",
                )

    if context.staged_okr_updates and not context.proposals:
        if _REJECT_RE.search(normalized):
            return PendingDecision(
                intent="reject_okr_updates",
                proposal_ids=[],
                confidence=0.9,
                reply_text=None,
                reason="single_domain_okr_reject",
            )
        if _AFFIRM_RE.search(normalized):
            return PendingDecision(
                intent="apply_okr_updates",
                proposal_ids=[],
                confidence=0.9,
                reply_text=None,
                reason="single_domain_okr_apply",
            )

    return None


async def _approve_proposals(
    session: AsyncSession,
    *,
    action_ids: list[int],
    actor: str,
    now: datetime,
) -> list[str]:
    results: list[str] = []
    for action_id in action_ids:
        row = await approve_proposed_action(session, action_id=action_id, actor=actor, now=now)
        if row is None:
            results.append(
                f"No pending proposal A{action_id} found (it may have already been handled)."
            )
            continue
        results.append(await _run_approved_action(session, row, actor))
    return results


async def _reject_proposals(
    session: AsyncSession,
    *,
    action_ids: list[int],
    actor: str,
    now: datetime,
) -> list[str]:
    results: list[str] = []
    for action_id in action_ids:
        row = await reject_proposed_action(session, action_id=action_id, actor=actor, now=now)
        if row is None:
            results.append(f"No pending proposal A{action_id} found.")
            continue
        results.append(f"Skipped — proposal A{action_id} ({row.action_type}) cancelled.")
    await session.commit()
    return results


async def _apply_staged_okr_updates(session: AsyncSession, *, slack_user_id: str) -> str:
    crumb = await get_live_okr_checkin_breadcrumb(session, slack_user_id)
    if crumb is None or not crumb.staged_updates:
        return "No staged OKR updates found."

    staged = [item for item in list(crumb.staged_updates) if isinstance(item, dict)]
    applied: list[str] = []
    try:
        for item in staged:
            kr_id = int(item["kr_id"])
            progress = int(round(float(item["progress"])))
            basis = str(item.get("basis") or "").strip()
            bullet = str(item.get("bullet") or "").strip() or basis[:200]
            await okr_repo.update_key_result(session, kr_id, prog=progress)
            activity_text = "updated via Friday check-in, approved by Jon" + (
                f" -- basis: {basis}" if basis else ""
            )
            await okr_repo.create_activity(
                session,
                kr_id=kr_id,
                text=activity_text,
                raw_text=basis or None,
            )
            await okr_repo.append_done_bullet(session, kr_id, bullet)
            applied.append(f"KR {kr_id} -> {progress}")
        await clear_staged_updates(session, crumb.id)
        await complete_okr_checkin_breadcrumb(session, crumb.id)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("natural_pending_router: failed applying staged OKR updates")
        return "Something went wrong applying the staged OKR updates."
    return f"Done. {', '.join(applied) if applied else 'No changes.'}"


async def _discard_staged_okr_updates(session: AsyncSession, *, slack_user_id: str) -> str:
    crumb = await get_live_okr_checkin_breadcrumb(session, slack_user_id)
    if crumb is None or not crumb.staged_updates:
        return "No staged OKR updates found."
    try:
        await clear_staged_updates(session, crumb.id)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("natural_pending_router: failed clearing staged OKR updates")
        return "Something went wrong clearing the staged OKR updates."
    return "Cleared, nothing changed."


def _proposal_short_label(row: ProposedAction) -> str:
    # Prefer the specific human preview (the event title, etc.) so multiple
    # proposals of the same type are distinguishable in a disambiguation prompt —
    # not "the calendar change, the calendar change, the calendar change".
    if row.preview:
        return row.preview
    if row.action_type == "slack.send":
        return "the Slack note"
    if row.action_type == "gmail.send":
        return "the email"
    if row.action_type.startswith("calendar."):
        return "the calendar change"
    if row.action_type == "jira.create":
        return "the Jira issue"
    return row.action_type


def _flatten_message_content(role: str, content: Any) -> str:
    if not isinstance(content, list):
        return ""
    blocks: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = str(item.get("text") or "").strip()
            if text:
                blocks.append(text)
    if not blocks:
        return ""
    return f"{role}: {' '.join(blocks)}"


def _flatten_blocks(content: list[Any]) -> str:
    blocks: list[str] = []
    for item in content:
        if isinstance(item, TextBlock):
            blocks.append(item.text)
        elif hasattr(item, "text"):
            blocks.append(str(item.text))
    return "\n".join(blocks)


def _has_explicit_domain_reference(
    text: str,
    context: PendingContext,
    decision: PendingDecision,
) -> bool:
    if _ID_TAG_RE.search(text):
        return True
    normalized = text.lower()
    if decision.intent in {"apply_okr_updates", "reject_okr_updates"}:
        return bool(_OKR_MARKER_RE.search(text))
    selected = [item for item in context.proposals if item.id in set(decision.proposal_ids)]
    for item in selected:
        if item.action_type == "slack.send" and "slack" in normalized:
            return True
        if item.action_type == "gmail.send" and ("email" in normalized or "gmail" in normalized):
            return True
        if item.action_type.startswith("calendar.") and "calendar" in normalized:
            return True
        if item.action_type == "jira.create" and "jira" in normalized:
            return True
    return False


def _default_clarification(context: PendingContext) -> str:
    proposal_labels = [item.short_label for item in context.proposals]
    if context.proposals and context.staged_okr_updates:
        if len(proposal_labels) == 1:
            return f"Did you mean approve {proposal_labels[0]}, or apply the staged OKR updates?"
        return "Did you mean one of the pending proposals, or the staged OKR updates?"
    if len(proposal_labels) == 1:
        return f"Did you mean approve {proposal_labels[0]}, or skip it?"
    if len(proposal_labels) > 1:
        labels = ", ".join(list(dict.fromkeys(proposal_labels))[:3])
        return f"Which one did you mean: {labels}, or more than one?"
    if context.staged_okr_updates:
        return "Did you want me to apply the staged OKR updates, or leave them alone?"
    return "Can you tell me which pending item you mean?"


def _default_action_reply(context: PendingContext, decision: PendingDecision) -> str:
    if decision.intent == "approve_proposals":
        selected = [item for item in context.proposals if item.id in set(decision.proposal_ids)]
        if len(selected) == 1:
            return f"Done. I handled {selected[0].short_label}."
        return "Done. I handled those pending proposals."
    if decision.intent == "reject_proposals":
        selected = [item for item in context.proposals if item.id in set(decision.proposal_ids)]
        if len(selected) == 1:
            return f"Okay. I skipped {selected[0].short_label}."
        return "Okay. I skipped those pending proposals."
    if decision.intent == "apply_okr_updates":
        return "Done. I applied the staged OKR updates."
    if decision.intent == "reject_okr_updates":
        return "Cleared, nothing changed."
    return "Done."


def _is_success_result(text: str) -> bool:
    lowered = text.lower()
    return (
        lowered.startswith("done")
        or lowered.startswith("okay")
        or lowered.startswith("cleared")
        or lowered.startswith("skipped")
    )
