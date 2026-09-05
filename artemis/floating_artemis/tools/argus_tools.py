"""Argus dispatch tool for Callie -- gated to agent_id='callie' only.

Callie owns this tool. No other agent sees it.

v1 (sync, retired): research_district ran in-turn; dossier returned directly.

v2 (async, retired): dispatch_research fired a background task immediately
(``loop.create_task``) and returned a short
``{"status":"dispatched","district":<name>}`` payload so Callie acknowledged
in the same turn.

v3 (resilient, retired): dispatch_research PERSISTED a ``pending`` row in
``argus_research_requests`` before firing that same background task, so a
process restart could recover it. This closed the "restart loses the work
entirely" gap but not the real one: the task created by ``loop.create_task``
ran inside ``python -m artemis.tools.mcp_server``, a SUBPROCESS spawned fresh
per Slack turn by ``artemis.providers.claude_code.adapter``. That subprocess
exits the moment the turn's tool-call response is sent, killing the task
mid-research every single time -- so research only ever completed when
``recover_pending_requests`` happened to re-fire it on a LATER app restart.
"Research runs when the app next happens to restart" is not a design, and the
tool still said ``"dispatched"`` -- true only if what came next was actually
running, which for five weeks it never was.

v4 (claimed dispatch, current -- ARGUS-1): dispatch_research now ONLY enqueues
a ``pending`` row and returns; it never creates a task and never runs
research. A claimer living in the long-lived FastAPI app process --
``run_claim_tick``, an APScheduler interval job started from ``main.lifespan``
-- atomically claims rows via
``UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING *``
(``_claim_next_request``) and runs them one at a time, in-process, with
per-row failure isolation. A row stuck at ``running`` past
``settings.argus_claim_stale_minutes`` (crash mid-research) is re-claimable.
``recover_pending_requests`` stays as a startup backstop -- it just runs one
claim tick immediately rather than waiting out the first scheduled interval --
not a second, independent mechanism. See
``briefs/argus-1-durable-dispatch.md``.

Also since ARGUS-1: when a dispatch supplies neither ``signal`` nor
``signal_id``, ``_resolve_latest_qualified_signal`` looks up the district's
newest qualified ``signal_queue`` row itself before enqueueing.
``_fetch_procurement``/``_fetch_state_doe``/``_fetch_usaspending``
(``artemis/argus/research.py``) all read the signal's ``state`` to know what
to search and find nothing (or, for USASpending, everything in the country)
without it -- every real Callie dispatch omitted it, so every dossier came
back thin on exactly the dimensions asked about most (current vendor,
decision makers). A caller-supplied signal is always used unchanged; this
lookup never overwrites it.

Channel ID resolution
---------------------
The tool reads the ``floating_session_id_var`` context variable (set by the turn
engine in chat.py) to get the current session_id. That session_id for Slack turns
has the form ``slack-callie-{team_id}-{channel_id}-{bucket}``, and is also stored
in the session row's metadata with an explicit ``channel_id`` key. The claimer
uses ``_session_channel_id`` from session_scope to extract the channel_id, and
reads ``team_id`` from the session metadata -- resolved in-turn, before the row
is persisted, so the values survive the tool's own process exiting.

Callie's token resolution
-------------------------
Posting (``_post_as_callie``) calls ``_resolve_agent_slack_config(session,
agent_id="callie", team_id=team_id)`` -- the same resolver used by
``_post_slack_message`` in ``integrations_slack_events.py`` -- to get the
access_token from the Callie Integration row in the DB.

Failure isolation
------------------
``_run_claimed_request`` -> ``_safe_research_and_post`` wraps the actual
research+post pipeline in try/except so any failure is logged at WARNING and
never propagates; the claim loop (``run_claim_tick``) additionally wraps EACH
claimed row's processing in its own try/except, so a completely unexpected
exception escaping that (a bug in the isolation itself) still cannot stop the
next row in the same tick from being tried.

Retry cap
---------
attempts >= 3 -> mark failed + post fallback, and the claim loop does not
retry it again. Attempts increments on two paths: a genuine failure inside
research (the row is released back to ``pending``, not left at ``running``),
and a stale-``running`` reclaim (presumed dead from a crash, since nothing
else advanced attempts for it) -- so a district that keeps crashing the app
mid-research is bounded by the same cap as one that keeps raising cleanly.

Retry backoff and claim order
-----------------------------
A released row is NOT immediately re-claimable: it waits
``settings.argus_claim_retry_backoff_minutes`` (from its ``claimed_at``, which
the failure stamps). ARGUS-1's own live smoke burned all three attempts in 52
seconds, because a tick drains until nothing is claimable and a just-failed
row was instantly claimable again -- so one transient Slack failure would
permanently fail a district. A never-attempted row (``attempts == 0``) skips
the backoff entirely; new work never waits out someone else's retry.

Claim order is ``(attempts, id)``, not ``id``. Under ``id`` alone a
persistently-failing district head-of-line blocked every newer one behind it,
which turns "one district is broken" into "the queue is stopped".
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]
from sqlalchemy import and_, case, or_, select, update

import artemis.db as _db
from artemis.agent.types import Tool
from artemis.argus.models import ArgusResearchRequest
from artemis.config import settings
from artemis.floating_artemis.authority import AuthorizedToolRegistry

_logger = logging.getLogger(__name__)

_SURFACE = "[surface:marketing-os]"
_AGENT_GATE = "[agent:callie]"

# Retry cap: after this many attempts, mark failed and post a fallback.
_MAX_ATTEMPTS = 3

__all__ = [
    "register_argus_tools",
    "recover_pending_requests",
    "run_claim_tick",
    "start_argus_claim_scheduler",
    "stop_argus_claim_scheduler",
]

# ── Tool definition ────────────────────────────────────────────────────────────

DISPATCH_RESEARCH = Tool(
    name="dispatch_research",
    description=(
        "Ask Argus (Callie's dedicated research agent) to research a district in depth. "
        "Returns immediately with a QUEUED acknowledgement -- research has not started "
        "yet and no completion time is promised. An in-app claimer picks the request up "
        "on its own schedule and Argus posts findings back to this channel once it "
        "finishes. "
        "Pass signal or signal_id whenever you have one: without it, two of Argus's "
        "five research sources (procurement timing, state DOE activity) have no state "
        "to search against and come back empty, so the dossier is thin on exactly the "
        "dimensions Jon asks about most (current vendor, decision makers). If both are "
        "omitted, this tool looks up the district's newest qualified signal itself before "
        "enqueueing -- but a signal you already have is more reliable than one it has to "
        "find, so supply it when you can. "
        "Each finding carries source='Argus' so attribution is grounded. "
        "Use when Jon asks Callie to dig into a district or a qualified signal. "
        "If the signal already carries a recent dossier this returns it with "
        "status='already_researched' and queues NOTHING -- report those findings "
        "rather than saying research is running. "
        f"{_SURFACE} {_AGENT_GATE} [layer:1]"
    ),
    input_schema={
        "type": "object",
        "required": ["district_key"],
        "properties": {
            "district_key": {
                "type": "string",
                "description": (
                    "Stable district identifier -- district_id from a signal "
                    "(e.g. 'TX-001') or a normalised district name slug. "
                    "Used as the drawer key; must match the signal's district_id "
                    "if one exists so findings accumulate correctly."
                ),
            },
            "signal_id": {
                "type": "integer",
                "description": (
                    "Signal ID that triggered this research, if you have one. "
                    "Strongly recommended: without it (and without signal below), "
                    "two of Argus's five research sources have no state to search "
                    "against and return nothing. When provided, every finding is "
                    "linked back to the signal as evidence so the provenance chain "
                    "is preserved. If both are omitted, this tool looks up the "
                    "district's newest qualified signal on its own before enqueueing."
                ),
            },
            "signal": {
                "type": "object",
                "description": (
                    "Triggering signal dict (from get_signal), if you have one. "
                    "Strongly recommended -- see signal_id: omitting this materially "
                    "thins the research. Provides state, headline, and provenance "
                    "context Argus's sources search against. Pass the full get_signal "
                    "output."
                ),
            },
            "force": {
                "type": "boolean",
                "description": (
                    "Re-run research even if this signal already has a recent "
                    "dossier attached. Default false: a completed dossier is "
                    "returned instead of paying for the same work again. Set true "
                    "only when the existing findings are genuinely stale for what "
                    "is being asked -- one signal was researched three times "
                    "because nothing recorded that it had been done."
                ),
            },
        },
    },
)

# ── Tool implementation ────────────────────────────────────────────────────────


async def _dispatch_research(inp: dict[str, Any]) -> str:
    """Enqueue-only: persist a pending row and return immediately.

    ARGUS-1: does NOT create a task and does NOT run research. The claimer
    (``run_claim_tick``, running in the long-lived app process) picks the row
    up on its own schedule -- see the module docstring's "v4" section for why
    firing a task from here (as v2/v3 did) never actually completed research:
    this call runs inside a per-turn MCP subprocess that exits before any task
    it created could finish.

    Returns a JSON payload like ``{"status":"queued","district":"TX-001"}``.
    Never "dispatched" or "running" -- those claims were the exact lie that
    cost five weeks (see the module docstring's "v3" section); "queued" is the
    truthful description of what this call actually did.
    """
    import json

    district_key = str(inp.get("district_key") or "").strip()
    if not district_key:
        return "Error: district_key is required"

    signal_id_raw = inp.get("signal_id")
    triggering_signal_id: str | None = (
        str(int(signal_id_raw)) if signal_id_raw is not None else None
    )
    signal: dict[str, Any] | None = inp.get("signal") or None

    # Capture the current session_id from the turn context.
    # floating_session_id_var is set by handle_turn in chat.py BEFORE the tool
    # is invoked, so it is always available here for Slack-originated turns.
    from artemis.floating_artemis.context import floating_session_id_var

    session_id: str | None = floating_session_id_var.get()

    _logger.info(
        "dispatch_research: enqueuing district_key=%r signal_id=%r session_id=%r",
        district_key,
        triggering_signal_id,
        session_id,
    )

    # ── Resolve channel_id + team_id now (in-turn) ─────────────────────────────
    channel_id, team_id = await _resolve_channel_and_team(session_id)

    if not channel_id:
        # NOT "queued". This path persists nothing, and for five weeks the
        # equivalent early-return said "dispatched" anyway: Callie relayed
        # "Argus is running" to Jon and to Josh on the strength of that return
        # value while argus_research_requests stayed empty. A tool that
        # reports success for work it did not do turns a plumbing bug into an
        # agent misleading a colleague, and the agent has no way to know
        # better.
        #
        # The underlying contextvar cause is fixed (artemis/tools/mcp_server.py
        # now sets floating_session_id_var inside the MCP subprocess, which
        # cannot inherit it). This stays fail-loud anyway: the next thing to
        # break this resolution must announce itself rather than be narrated
        # as success.
        _logger.error(
            "dispatch_research: NOT QUEUED — no channel_id resolved for "
            "session_id=%r, so there is nowhere to post findings. Nothing was "
            "persisted.",
            session_id,
        )
        return json.dumps(
            {
                "status": "failed",
                "district": district_key,
                "error": "no_channel_resolved",
                "detail": (
                    "Research was NOT queued. I could not work out which Slack "
                    "channel to post findings to, so nothing was saved and "
                    "nothing will run. Say so plainly rather than reporting "
                    "this as queued, and do not promise findings later."
                ),
            }
        )

    # ── Auto-resolve a signal when the caller didn't supply one ────────────────
    # A caller-supplied signal/signal_id is always used unchanged and never
    # overwritten by this lookup -- see _resolve_latest_qualified_signal's
    # docstring for why omitting it starves the research.
    if signal is None and triggering_signal_id is None:
        try:
            resolved = await _resolve_latest_qualified_signal(district_key)
        except Exception:
            _logger.warning(
                "dispatch_research: signal auto-resolution errored for "
                "district_key=%r -- enqueuing without signal context",
                district_key,
                exc_info=True,
            )
            resolved = None
        if resolved is not None:
            signal, triggering_signal_id = resolved
            _logger.info(
                "dispatch_research: auto-resolved qualified signal_id=%s for "
                "district_key=%r (caller supplied neither signal nor signal_id)",
                triggering_signal_id,
                district_key,
            )
        else:
            _logger.info(
                "dispatch_research: no qualified signal found for "
                "district_key=%r -- enqueuing without signal context (the "
                "dossier will likely be thin on procurement/state-DOE "
                "dimensions; this is recorded by signal staying null on the "
                "persisted row rather than by a separate column)",
                district_key,
            )

    # ── Already researched recently? Then say so instead of paying again ──────
    # Signal 3186 was researched three separate times -- 31 Aug twice, 4 Sep once
    # -- each run completing successfully and none aware of the others, because
    # nothing recorded the result anywhere the next request would look.
    prior = await existing_dossier(triggering_signal_id)
    if prior and not bool(inp.get("force")):
        return json.dumps(
            {
                "status": "already_researched",
                "district": district_key,
                "completed_at": prior.get("completed_at"),
                "request_id": prior.get("request_id"),
                "excerpt": prior.get("excerpt"),
                "detail": (
                    "Research on this signal already completed and is attached to "
                    "it. Nothing new was queued. Use the findings below rather "
                    "than telling anyone research is running, and do not promise "
                    "fresh findings. Pass force=true only if the existing dossier "
                    "is genuinely stale for what is being asked."
                ),
            }
        )

    # ── Persist the pending row -- this call does nothing else ─────────────────
    request_id = await _insert_pending_request(
        district_key=district_key,
        channel_id=channel_id,
        team_id=team_id,
        signal=signal,
        triggering_signal_id=triggering_signal_id,
    )

    if request_id is None:
        # _insert_pending_request already logged why. Unlike v2/v3, there is no
        # in-process fallback anymore: nothing was created to run this request,
        # so a persist failure means the work is dropped, full stop. Reporting
        # "queued" here would repeat the exact mistake this slice exists to fix.
        _logger.error(
            "dispatch_research: NOT QUEUED — failed to persist a pending row "
            "for district_key=%r; there is no in-process fallback (ARGUS-1), "
            "so nothing will ever run this request.",
            district_key,
        )
        return json.dumps(
            {
                "status": "failed",
                "district": district_key,
                "error": "persist_failed",
                "detail": (
                    "Research was NOT queued. I could not save the request, "
                    "so nothing is running and nothing will run later. Say so "
                    "plainly and don't promise findings."
                ),
            }
        )

    return json.dumps(
        {
            "status": "queued",
            "district": district_key,
            "detail": (
                "Research is queued. It hasn't started yet and I can't promise "
                "when it'll finish -- Argus will post findings to this channel "
                "once the in-app claimer picks it up and completes it."
            ),
        }
    )


# ── Persistence helpers ────────────────────────────────────────────────────────


async def _resolve_channel_and_team(session_id: str | None) -> tuple[str | None, str]:
    """Resolve channel_id and team_id from a Slack session_id.

    Returns (channel_id, team_id). channel_id may be None if resolution fails.
    This runs in-turn (before the background task) so the resolved values are
    captured in the persistent row and survive across process restarts.
    """
    if not session_id:
        return None, ""

    try:
        from artemis.floating_artemis import repository as fa_repo
        from artemis.floating_artemis.session_scope import _session_channel_id

        async with _db.SessionLocal() as _sess:
            try:
                row = await fa_repo.get_session_by_id(_sess, session_id)
                metadata: dict[str, Any] = row.metadata_ if isinstance(row.metadata_, dict) else {}
            except Exception:
                metadata = {}

        channel_id = _session_channel_id(session_id, metadata)
        team_id = str(metadata.get("team_id") or "")
        return channel_id, team_id
    except Exception:
        _logger.warning(
            "dispatch_research: could not resolve channel/team from session_id=%r",
            session_id,
            exc_info=True,
        )
        return None, ""


async def _resolve_latest_qualified_signal(
    district_key: str,
) -> tuple[dict[str, Any], str] | None:
    """Look up the newest qualified ``signal_queue`` row for ``district_key``.

    Finding (2026-08-12): ``_fetch_procurement``, ``_fetch_state_doe``, and
    ``_fetch_usaspending`` (``artemis/argus/research.py``) all read ``state``
    straight off the signal dict -- absent, procurement/state-DOE search
    nothing and USASpending searches the entire country. Every real Callie
    dispatch passed ``signal_id=None`` (visible in this module's own log line
    at dispatch time), so every one of them came back thin on exactly the
    dimensions asked about most (current vendor, decision makers) even after
    the contextvar fix made the plumbing work. Called from
    ``_dispatch_research`` only when the caller supplied neither ``signal``
    nor ``signal_id``; a caller-supplied signal is always used unchanged and
    is never overwritten by this lookup.

    Returns ``(signal_dict, signal_id_str)`` for the newest
    ``signal_queue`` row with ``signal_status='qualified'`` and
    ``district_id == district_key``, or ``None`` if none qualifies -- in which
    case the caller still enqueues, just without signal context (see
    ``_dispatch_research``). The dict shape matches the one
    ``_research_and_post`` already builds from a bare ``signal_id`` lookup
    (``headline``/``state``/``district_id``/``source_url``), plus
    ``provenance`` so ``_fetch_board_minutes``'s ``boarddocs_url`` lookup
    still works when it's present.

    ``district_key`` is compared against ``signal_queue.district_id`` (the
    free-text provenance column), not ``resolved_district_id`` (the int FK) --
    the same convention ``dispatch_research``'s own schema docstring and
    ``artemis.marketing.brief_assembler._resolve_district_key`` already use
    for this identifier.
    """
    from artemis.marketing.models import SignalQueue

    async with _db.SessionLocal() as session:
        result = await session.execute(
            select(SignalQueue)
            .where(
                SignalQueue.district_id == district_key,
                SignalQueue.signal_status == "qualified",
            )
            .order_by(SignalQueue.created_at.desc(), SignalQueue.id.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()

    if row is None:
        return None

    signal: dict[str, Any] = {
        "headline": row.headline or "",
        "state": row.state or "",
        "district_id": row.district_id or "",
        "source_url": row.source_url or "",
    }
    if isinstance(row.provenance, dict):
        signal["provenance"] = row.provenance
    return signal, str(row.id)


async def _insert_pending_request(
    *,
    district_key: str,
    channel_id: str,
    team_id: str,
    signal: dict[str, Any] | None,
    triggering_signal_id: str | None,
) -> int | None:
    """Insert a pending row into argus_research_requests; return the row id."""
    try:
        async with _db.SessionLocal() as session:
            row = ArgusResearchRequest(
                district_key=district_key,
                channel_id=channel_id,
                team_id=team_id,
                signal=signal,
                triggering_signal_id=triggering_signal_id,
                status="pending",
                attempts=0,
            )
            session.add(row)
            await session.flush()
            row_id: int = row.id
            await session.commit()
        _logger.info(
            "dispatch_research: persisted pending request id=%s for district_key=%r",
            row_id,
            district_key,
        )
        return row_id
    except Exception:
        _logger.warning(
            "dispatch_research: failed to persist pending request for district_key=%r — "
            "proceeding without persistence (restart recovery will not apply)",
            district_key,
            exc_info=True,
        )
        return None


#: How long a dossier counts as current. Below this, a second request for the
#: same signal is answered from the existing one instead of re-run.
DOSSIER_FRESH_DAYS = 14


async def _attach_dossier_to_signal(
    triggering_signal_id: str | None,
    *,
    request_id: int | None,
    summary: Any = None,
) -> None:
    """Record on the SIGNAL that research completed, and what it said.

    **Why this exists.** Argus wrote its findings to memory and posted them to
    Slack, and neither is attached to the thing that triggered it. So nothing can
    answer "has this district been researched?" -- signal 3186 was researched
    three separate times (31 Aug twice, 4 Sep once), each run completing
    successfully, each one unaware of the last.

    It is also why Callie could say "the signal is still qualified with no
    dossier attached" about work that had finished ninety minutes earlier. She
    was reading the signal, and the signal did not know.
    """
    if triggering_signal_id is None:
        return
    try:
        from sqlalchemy.orm.attributes import flag_modified

        # pipeline_runs is imported for its side effect: signal_queue carries a
        # foreign key to it, and mapping SignalQueue without it raises
        # NoReferencedTableError. The app process happens to import it already,
        # so this only bites callers with a narrower import graph -- which is
        # exactly the sort of thing that works until it does not.
        import artemis.pipelines.models  # noqa: F401
        from artemis.marketing.models import SignalQueue

        async with _db.SessionLocal() as session:
            row = await session.get(SignalQueue, int(triggering_signal_id))
            if row is None:
                return
            provenance = dict(row.provenance or {})
            provenance["argus_dossier"] = {
                "request_id": request_id,
                "completed_at": datetime.now(UTC).isoformat(),
                # An excerpt, not the whole dossier: the full text is in memory
                # and in the Slack post. This field answers "was this done, when,
                # and roughly what did it say" without duplicating the record.
                # Either the rendered Callie post or the raw research dict,
                # depending on which branch completed; both are worth keeping and
                # neither is worth a second code path.
                "excerpt": (str(summary) if summary else "")[:1500] or None,
            }
            row.provenance = provenance
            # JSONB mutated in place does not mark the row dirty -- the UPDATE is
            # silently dropped without this (see CLAUDE.md on node_states).
            flag_modified(row, "provenance")
            await session.commit()
            _logger.info(
                "dispatch_research: attached dossier to signal %s (request %s)",
                triggering_signal_id,
                request_id,
            )
    except Exception:
        _logger.warning(
            "dispatch_research: could not attach dossier to signal %s",
            triggering_signal_id,
            exc_info=True,
        )


async def existing_dossier(triggering_signal_id: str | None) -> dict[str, Any] | None:
    """Return a recent dossier for this signal, or None.

    Lets a caller answer "already researched, here is when" instead of paying for
    the same research a third time.
    """
    if triggering_signal_id is None:
        return None
    try:
        import artemis.pipelines.models  # noqa: F401
        from artemis.marketing.models import SignalQueue

        async with _db.SessionLocal() as session:
            row = await session.get(SignalQueue, int(triggering_signal_id))
        dossier = (row.provenance or {}).get("argus_dossier") if row is not None else None
        if not isinstance(dossier, dict):
            return None
        completed = dossier.get("completed_at")
        if not completed:
            return None
        age = datetime.now(UTC) - datetime.fromisoformat(str(completed))
        return dossier if age.days < DOSSIER_FRESH_DAYS else None
    except Exception:
        _logger.warning(
            "dispatch_research: could not read dossier for signal %s",
            triggering_signal_id,
            exc_info=True,
        )
        return None


async def _mark_request_done(
    request_id: int | None,
    *,
    triggering_signal_id: str | None = None,
    summary: Any = None,
) -> None:
    """Mark a request row as done, and record the result on the triggering signal."""
    if request_id is None:
        return
    try:
        async with _db.SessionLocal() as session:
            row = await session.get(ArgusResearchRequest, request_id)
            if row is not None:
                row.status = "done"
                row.completed_at = datetime.now(UTC)
                await session.commit()
    except Exception:
        _logger.warning(
            "dispatch_research: failed to mark request id=%s done",
            request_id,
            exc_info=True,
        )

    # Separate session and separate try: the signal update failing must not
    # leave the request looking unfinished and get it retried.
    await _attach_dossier_to_signal(triggering_signal_id, request_id=request_id, summary=summary)


async def _mark_request_failed(
    request_id: int | None,
    *,
    error: str,
    channel_id: str,
    team_id: str,
    district_key: str,
) -> bool:
    """Increment attempts; if >= _MAX_ATTEMPTS mark failed and return True (should post fallback).

    ARGUS-1: on a non-cap failure this now releases the row back to
    ``pending`` (it was left at whatever status the caller set -- 'running',
    since ARGUS-1 -- in the pre-claimer design, where the only two statuses
    were 'pending' going in and 'done'/'failed' coming out, so there was
    nothing to release). Leaving it at 'running' here would strand it until
    ``settings.argus_claim_stale_minutes`` elapses AND would double-count the
    attempt when the stale-reclaim path increments attempts again for the
    same failure. Releasing to 'pending' makes it immediately reclaimable by
    the very next poll tick with attempts counted exactly once.
    """
    if request_id is None:
        return False
    try:
        async with _db.SessionLocal() as session:
            row = await session.get(ArgusResearchRequest, request_id)
            if row is None:
                return False
            row.attempts = (row.attempts or 0) + 1
            row.error = error[:2000] if error else None
            if row.attempts >= _MAX_ATTEMPTS:
                row.status = "failed"
                await session.commit()
                return True  # caller should post fallback
            row.status = "pending"  # release the claim; next tick retries immediately
            await session.commit()
            return False
    except Exception:
        _logger.warning(
            "dispatch_research: failed to update request id=%s on error",
            request_id,
            exc_info=True,
        )
        return False


# ── Research + post pipeline (runs inside the app-process claimer) ─────────────


async def _safe_research_and_post(
    *,
    request_id: int | None,
    channel_id: str,
    team_id: str,
    district_key: str,
    triggering_signal_id: str | None,
    signal: dict[str, Any] | None,
) -> None:
    """Run research + Callie-voiced summary + Slack post. Swallows all exceptions."""
    try:
        await _research_and_post(
            request_id=request_id,
            channel_id=channel_id,
            team_id=team_id,
            district_key=district_key,
            triggering_signal_id=triggering_signal_id,
            signal=signal,
        )
    except Exception as exc:
        _logger.warning(
            "dispatch_research: background task failed for district_key=%r "
            "request_id=%s channel_id=%r (non-fatal)",
            district_key,
            request_id,
            channel_id,
            exc_info=True,
        )
        should_post_fallback = await _mark_request_failed(
            request_id,
            error=str(exc),
            channel_id=channel_id,
            team_id=team_id,
            district_key=district_key,
        )
        if should_post_fallback:
            _logger.warning(
                "dispatch_research: request_id=%s reached max attempts (%d) for district_key=%r — "
                "posting fallback to channel_id=%r",
                request_id,
                _MAX_ATTEMPTS,
                district_key,
                channel_id,
            )
            await _post_fallback(
                channel_id=channel_id,
                team_id=team_id,
                district_key=district_key,
            )


async def _research_and_post(
    *,
    request_id: int | None,
    channel_id: str,
    team_id: str,
    district_key: str,
    triggering_signal_id: str | None,
    signal: dict[str, Any] | None,
) -> None:
    """Core background logic: research → LLM summary → Slack post → mark done."""
    # ── 1. Optionally fetch signal row if only signal_id provided ─────────────
    if signal is None and triggering_signal_id is not None:
        try:
            from artemis.marketing import repository as _repo

            async with _db.SessionLocal() as _sess:
                sig_row = await _repo.get_signal(_sess, int(triggering_signal_id))
            signal = {
                "headline": sig_row.headline or "",
                "state": sig_row.state or "",
                "district_id": sig_row.district_id or "",
                "source_url": sig_row.source_url or "",
            }
        except Exception as exc:
            _logger.warning(
                "dispatch_research: could not fetch signal_id=%s -- %s (continuing without signal context)",
                triggering_signal_id,
                exc,
            )

    # ── 2. Run research_district (background DB session) ──────────────────────
    _logger.info(
        "dispatch_research (bg): starting research for district_key=%r channel_id=%r request_id=%s",
        district_key,
        channel_id,
        request_id,
    )

    from artemis.argus.flow import research_district

    async with _db.SessionLocal() as session:
        summary = await research_district(
            session,
            district_key=district_key,
            signal=signal,
            triggering_signal_id=triggering_signal_id,
        )
        await session.commit()

    # ── 3. Produce a Callie-voiced summary via LLM ────────────────────────────
    callie_post = await _callie_summarize(district_key=district_key, summary=summary)

    # Apply Slack formatting + linting
    from artemis.writing_rules import lint_agent_text, md_to_mrkdwn

    formatted_text = md_to_mrkdwn(lint_agent_text(callie_post))
    if not formatted_text.strip():
        _logger.warning(
            "dispatch_research: formatted Callie post is empty for district_key=%r; skipping Slack post",
            district_key,
        )
        await _mark_request_done(
            request_id, triggering_signal_id=triggering_signal_id, summary=summary
        )
        return

    # ── 4. Post back to channel as Callie ─────────────────────────────────────
    await _post_as_callie(
        channel_id=channel_id,
        team_id=team_id,
        text=formatted_text,
    )

    _logger.info(
        "dispatch_research (bg): posted Argus findings to channel_id=%r for district_key=%r request_id=%s",
        channel_id,
        district_key,
        request_id,
    )

    # ── 5. Mark request done, and record it on the signal that triggered it ───
    await _mark_request_done(
        request_id, triggering_signal_id=triggering_signal_id, summary=formatted_text
    )


# ── Claimer (ARGUS-1: the actual mechanism -- everything below runs in the ────
# ── long-lived app process, never in the per-turn MCP subprocess) ─────────────


@dataclass(frozen=True)
class _ClaimedRequest:
    """A row this process just atomically claimed. Plain dataclass, not the ORM
    row itself -- the claiming session is closed by the time callers use this,
    and named-field access avoids the positional-row-unpacking trap (see
    CLAUDE.md)."""

    id: int
    district_key: str
    channel_id: str
    team_id: str
    signal: dict[str, Any] | None
    triggering_signal_id: str | None
    attempts: int


async def _claim_next_request() -> _ClaimedRequest | None:
    """Atomically claim exactly one pending-or-stale-running row, or ``None``.

    ``UPDATE argus_research_requests SET status='running', claimed_at=now()
    WHERE id = (SELECT id FROM argus_research_requests WHERE <claimable>
    ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *`` -- the brief's
    own suggested shape. ``<claimable>`` is ``status='pending'`` OR
    (``status='running'`` AND ``claimed_at`` older than
    ``settings.argus_claim_stale_minutes`` -- presumed orphaned by a crash
    mid-research).

    Two invariants this single statement provides, both load-bearing and
    neither optional:

    1. Two concurrent claimers can never take the same row. ``FOR UPDATE SKIP
       LOCKED`` is a Postgres row lock, not an in-process lock -- it holds
       across separate connections/transactions/processes, so this is true
       even if two app instances ran this query in the same microsecond. If
       the subquery's row is locked by another in-flight claim, this claimer
       skips it and either claims a different claimable row or gets nothing.
    2. A claim can never "not complete" and leave a row invisible to future
       claims -- there is no separate SELECT-then-UPDATE window for a crash to
       land in between; claiming a row atomically FLIPS it to 'running' in the
       same statement that read it.

    Attempts is incremented HERE, conditionally, only for the stale-running
    branch (``CASE WHEN status='running' THEN attempts+1 ELSE attempts``) --
    a fresh 'pending' claim leaves attempts untouched (it was already
    incremented, if at all, by ``_mark_request_failed`` on the failure that
    put it back to 'pending'). This is what lets a crash-looping district hit
    the same ``_MAX_ATTEMPTS`` cap as a cleanly-failing one without double
    counting either way -- see ``_mark_request_failed``'s docstring.

    Returns a ``_ClaimedRequest`` regardless of whether attempts is already
    at or past the cap -- ``_run_claimed_request`` is what decides whether to
    actually run it or finalize it as exhausted, so this function's contract
    stays simple: "claimed something claimable, or nothing was claimable."
    """
    stale_cutoff = datetime.now(UTC) - timedelta(minutes=settings.argus_claim_stale_minutes)

    # Backoff before a RETRY is claimable again. A failure releases the row to
    # 'pending' with claimed_at stamped, so "pending and attempted and claimed
    # recently" means "just failed, leave it alone for now". A never-attempted
    # row has attempts == 0 and is claimable immediately -- new work must not
    # wait out anyone else's backoff.
    #
    # Without this, the ARGUS-1 live smoke burned all three attempts in 52
    # seconds: the tick drains until nothing is claimable, and a just-failed row
    # was instantly claimable again. Retries exist to outlast transient
    # conditions, and three of them inside a minute outlast nothing -- one Slack
    # blip would permanently fail a district.
    retry_cutoff = datetime.now(UTC) - timedelta(minutes=settings.argus_claim_retry_backoff_minutes)
    claimable_id = (
        select(ArgusResearchRequest.id)
        .where(
            or_(
                and_(
                    ArgusResearchRequest.status == "pending",
                    or_(
                        ArgusResearchRequest.attempts == 0,
                        ArgusResearchRequest.claimed_at.is_(None),
                        ArgusResearchRequest.claimed_at < retry_cutoff,
                    ),
                ),
                and_(
                    ArgusResearchRequest.status == "running",
                    ArgusResearchRequest.claimed_at.isnot(None),
                    ArgusResearchRequest.claimed_at < stale_cutoff,
                ),
            )
        )
        # attempts first, then id. ORDER BY id alone let one persistently-failing
        # district head-of-line block every newer one behind it, which is the
        # difference between "one district is broken" and "the queue is stopped".
        # Ordering by attempts sends never-tried work ahead of anything retrying.
        .order_by(ArgusResearchRequest.attempts, ArgusResearchRequest.id)
        .with_for_update(skip_locked=True)
        .limit(1)
        .scalar_subquery()
    )

    stmt = (
        update(ArgusResearchRequest)
        .where(ArgusResearchRequest.id == claimable_id)
        .values(
            status="running",
            claimed_at=datetime.now(UTC),
            attempts=case(
                (ArgusResearchRequest.status == "running", ArgusResearchRequest.attempts + 1),
                else_=ArgusResearchRequest.attempts,
            ),
        )
        .returning(ArgusResearchRequest)
    )

    async with _db.SessionLocal() as session:
        result = await session.execute(stmt)
        row = result.scalars().first()
        await session.commit()

    if row is None:
        return None

    return _ClaimedRequest(
        id=row.id,
        district_key=row.district_key,
        channel_id=row.channel_id,
        team_id=row.team_id or "",
        signal=row.signal,
        triggering_signal_id=row.triggering_signal_id,
        attempts=row.attempts,
    )


async def _finalize_exhausted_claim(claimed: _ClaimedRequest, *, reason: str) -> None:
    """Mark an already-claimed row 'failed' WITHOUT running research or
    incrementing attempts again (``_claim_next_request`` already incremented
    it to reach the cap) -- then post the same fallback a normal cap-out
    would. Used only when a reclaim lands at/past ``_MAX_ATTEMPTS`` on a
    stale-running row, i.e. a district that keeps crashing the app itself,
    not merely raising."""
    try:
        async with _db.SessionLocal() as session:
            row = await session.get(ArgusResearchRequest, claimed.id)
            if row is not None:
                row.status = "failed"
                row.error = reason[:2000]
                await session.commit()
    except Exception:
        _logger.warning(
            "argus claim: failed to finalize exhausted request_id=%s district_key=%r",
            claimed.id,
            claimed.district_key,
            exc_info=True,
        )
    await _post_fallback(
        channel_id=claimed.channel_id,
        team_id=claimed.team_id,
        district_key=claimed.district_key,
    )


async def _run_claimed_request(claimed: _ClaimedRequest) -> None:
    """Run (or finalize as exhausted) one claimed row. Never raises."""
    if claimed.attempts >= _MAX_ATTEMPTS:
        _logger.warning(
            "argus claim: request_id=%s district_key=%r reclaimed already at "
            "attempts=%d (cap %d) -- treating as exhausted without another "
            "research attempt (repeated crash mid-research, not a clean "
            "failure -- those release to 'pending' below the cap)",
            claimed.id,
            claimed.district_key,
            claimed.attempts,
            _MAX_ATTEMPTS,
        )
        await _finalize_exhausted_claim(
            claimed,
            reason=(
                f"exceeded max attempts ({_MAX_ATTEMPTS}) after being reclaimed from a "
                "stale 'running' state -- likely a repeated crash mid-research rather "
                "than a clean failure"
            ),
        )
        return

    await _safe_research_and_post(
        request_id=claimed.id,
        channel_id=claimed.channel_id,
        team_id=claimed.team_id,
        district_key=claimed.district_key,
        triggering_signal_id=claimed.triggering_signal_id,
        signal=claimed.signal,
    )


_claim_lock = asyncio.Lock()
_claim_scheduler: AsyncIOScheduler | None = None
_CLAIM_JOB_ID = "argus_claim_poll"
# Defense in depth, not an expected ceiling: a drain loop that claims until
# nothing is left claimable should never need this many iterations in one
# tick, but an unbounded loop here would starve every OTHER scheduled job in
# this process if the table ever grew a pathological backlog.
_MAX_ROWS_PER_TICK = 25


def get_argus_claim_scheduler() -> AsyncIOScheduler:
    global _claim_scheduler
    if _claim_scheduler is None:
        _claim_scheduler = AsyncIOScheduler()
    return _claim_scheduler


def start_argus_claim_scheduler() -> None:
    """Register the claim-poll job and start the scheduler. Called from FastAPI lifespan."""
    scheduler = get_argus_claim_scheduler()
    scheduler.add_job(
        run_claim_tick,
        trigger=IntervalTrigger(seconds=settings.argus_claim_poll_interval_seconds),
        id=_CLAIM_JOB_ID,
        replace_existing=True,
        max_instances=1,  # defense in depth alongside _claim_lock
        misfire_grace_time=60,
    )
    if not scheduler.running:
        scheduler.start()
        _logger.info(
            "argus claim scheduler started (interval=%ds, stale_after=%dmin)",
            settings.argus_claim_poll_interval_seconds,
            settings.argus_claim_stale_minutes,
        )


def stop_argus_claim_scheduler() -> None:
    """Stop the scheduler. Called from FastAPI lifespan shutdown."""
    global _claim_scheduler
    if _claim_scheduler is not None and _claim_scheduler.running:
        _claim_scheduler.shutdown(wait=False)
        _logger.info("argus claim scheduler stopped")
    _claim_scheduler = None


async def run_claim_tick() -> None:
    """Scheduler entry point. Skips (logs INFO) if the previous tick is still running.

    Also the entry point ``recover_pending_requests`` calls at startup -- see
    that function's docstring for why sharing this exact entry point (rather
    than a separate startup-only code path) is what makes "cannot double-run
    a row the claimer already holds" true by construction rather than by
    care taken to keep two paths in sync.
    """
    if _claim_lock.locked():
        _logger.info("argus claim: previous tick still running -- skipping this tick")
        return
    async with _claim_lock:
        processed = 0
        while processed < _MAX_ROWS_PER_TICK:
            try:
                claimed = await _claim_next_request()
            except Exception:
                _logger.warning(
                    "argus claim: the claim query itself failed -- stopping this "
                    "tick early; the next tick retries",
                    exc_info=True,
                )
                break
            if claimed is None:
                break
            processed += 1
            try:
                await _run_claimed_request(claimed)
            except Exception:
                # Should be unreachable -- _run_claimed_request and everything
                # it calls already swallow their own exceptions. Caught anyway
                # so a bug in that isolation itself still cannot stop the next
                # claimable row in this tick from being tried, per the brief's
                # "a failure on one row does not stop the claimer processing
                # the next" requirement.
                _logger.warning(
                    "argus claim: unhandled error processing request_id=%s "
                    "district_key=%r -- continuing to the next claimable row",
                    claimed.id,
                    claimed.district_key,
                    exc_info=True,
                )
        if processed:
            _logger.info("argus claim: processed %d request(s) this tick", processed)


async def recover_pending_requests() -> None:
    """Startup backstop -- runs one claim tick immediately. NOT a second mechanism.

    Called once at app startup (from the FastAPI lifespan hook in main.py, via
    ``asyncio.create_task`` so it does not delay startup).

    Before ARGUS-1 this independently re-fired a background task per
    'pending' row, bypassing the atomic claim entirely -- correct only
    because it ran once, before the interval scheduler existed to race it.
    Now it just calls ``run_claim_tick`` -- the exact entry point the interval
    scheduler uses, including its skip-if-already-running guard -- so a row
    the claimer already holds cannot be double-run: either this IS the atomic
    SELECT ... FOR UPDATE SKIP LOCKED claim (so it cannot grab a row a
    concurrently-running tick already holds), or the in-process lock is held
    and this call returns immediately without touching the table at all.

    The reason to keep this at all rather than only relying on the interval:
    a cold start with a backlog (the app was down for a while) gets that
    backlog claimed right away instead of waiting out
    ``settings.argus_claim_poll_interval_seconds`` for no reason.
    """
    try:
        await run_claim_tick()
    except Exception:
        _logger.warning(
            "argus startup_recovery: claim tick failed -- the next scheduled tick will retry",
            exc_info=True,
        )


# ── Callie summarize + post helpers ───────────────────────────────────────────


async def _callie_summarize(
    *,
    district_key: str,
    summary: dict[str, Any],
) -> str:
    """Produce a Callie-voiced summary of the Argus dossier via an LLM pass.

    Uses complete_with_fallback (codex primary → claude-code fallback) so it
    works on the Claude Code subscription surface. Falls back to a structured
    plain-text summary on any LLM error.
    """
    from artemis.floating_artemis.personality import CALLIE_PERSONA_CORE

    new_findings: int = summary.get("new_findings", 0)
    gap_dims: list[str] = summary.get("gap_dimensions", [])
    existing_dims: list[str] = summary.get("existing_dimensions", [])
    angle: str | None = summary.get("recommended_angle")

    is_thin = new_findings == 0 and not angle

    # Build a dossier brief for the LLM
    dossier_lines: list[str] = [
        f"District: {district_key}",
        f"New findings from Argus: {new_findings}",
    ]
    if gap_dims:
        dossier_lines.append(f"Dimensions researched: {', '.join(gap_dims)}")
    if existing_dims:
        dossier_lines.append(f"Previously known dimensions: {', '.join(existing_dims)}")
    if angle:
        dossier_lines.append(f"Recommended outreach angle: {angle}")
    if is_thin:
        dossier_lines.append("(Argus found no new material on this district.)")

    dossier_text = "\n".join(dossier_lines)

    if is_thin:
        summary_instruction = (
            "Argus came back light on this district. Write a brief, honest note to Jon "
            "in Callie's voice: acknowledge what Argus looked for, note the gap, and "
            "suggest a next step (e.g. 'we can revisit when a signal surfaces'). "
            "Keep it to 2-3 sentences. Credit Argus by name."
        )
    else:
        summary_instruction = (
            "Write a Slack message in Callie's voice summarising Argus's findings for this district. "
            "Lead with the single most critical data point. Then: what we know, the recommended "
            "angle, and any open flags. Credit Argus by name. Be concrete and direct. "
            "No bullet soup. Use bold labels sparingly. No em or en dashes. No emojis. "
            "2-4 short paragraphs max."
        )

    prompt = (
        f"Argus has finished researching district {district_key!r}. "
        f"Here is the research dossier summary:\n\n{dossier_text}\n\n"
        f"{summary_instruction}"
    )

    try:
        from artemis.agent.client import CompletionRequest
        from artemis.agent.types import Message, TextBlock
        from artemis.providers.fallback import complete_with_fallback

        req = CompletionRequest(
            messages=[Message(role="user", content=[TextBlock(text=prompt)])],
            system=CALLIE_PERSONA_CORE,
            model=None,  # let each adapter use its default
            max_tokens=600,
            cache_system=False,
            cache_tools=False,
        )
        # Voice-critical: this is Callie relaying Argus's findings to Jon, so it
        # runs on claude-code (her voice) — NOT codex. claude-code is free
        # (subscription), so there's no cost reason to use codex here.
        resp = await complete_with_fallback(
            req,
            primary="claude-code",
            fallback="claude-code",
            feature_tag="argus_callie_summary",
        )
        text = ""
        for block in resp.message.content:
            if hasattr(block, "text"):
                text += block.text
        text = text.strip()
        if text:
            return text
    except Exception:
        _logger.warning(
            "dispatch_research: Callie LLM summary failed for district_key=%r — using plain fallback",
            district_key,
            exc_info=True,
        )

    # Plain fallback (no LLM)
    if is_thin:
        return (
            f"Argus came back light on {district_key} — no new material surfaced this pass. "
            "We can revisit when a stronger signal comes through."
        )

    lines = [f"Argus is back with findings on {district_key}."]
    if angle:
        lines.append(f"*Recommended angle:* {angle}")
    if gap_dims:
        lines.append(f"*Dimensions covered:* {', '.join(gap_dims)}")
    lines.append("Findings are in the district drawer (workspace:marketing scope). Source: Argus.")
    return "\n\n".join(lines)


async def _post_as_callie(
    *,
    channel_id: str,
    team_id: str,
    text: str,
) -> None:
    """Post a message to Slack as Callie.

    Reuses ``_resolve_agent_slack_config`` from integrations_slack_events — the
    same resolver that handles all Callie inbound events — to fetch Callie's
    access_token from the Integration row in the DB. Then uses SlackClient to
    post.
    """
    from artemis.integrations.slack.client import SlackClient
    from artemis.routes.integrations_slack_events import _resolve_agent_slack_config

    async with _db.SessionLocal() as session:
        agent_cfg = await _resolve_agent_slack_config(
            session,
            agent_id="callie",
            team_id=team_id or None,
        )

    if not agent_cfg.access_token:
        _logger.warning(
            "dispatch_research: no Slack access token configured for agent=callie; "
            "cannot post Argus findings to channel_id=%r",
            channel_id,
        )
        return

    client = SlackClient(token=agent_cfg.access_token)
    await client.post_message(channel=channel_id, text=text)


async def _post_fallback(
    *,
    channel_id: str,
    team_id: str,
    district_key: str,
) -> None:
    """Post a fallback message when a request has exhausted all retry attempts."""
    try:
        await _post_as_callie(
            channel_id=channel_id,
            team_id=team_id,
            text=(
                f"Argus couldn't complete the dig on {district_key} after {_MAX_ATTEMPTS} attempts "
                "— flagging it. I'll retry when you ask again."
            ),
        )
    except Exception:
        _logger.warning(
            "dispatch_research: _post_fallback itself failed for district_key=%r channel_id=%r",
            district_key,
            channel_id,
            exc_info=True,
        )


# ── Registry helper ────────────────────────────────────────────────────────────


def register_argus_tools(registry: AuthorizedToolRegistry) -> None:
    """Register Argus tools into the provided registry.

    Called only when agent_id == 'callie' (enforced in tool_registry.py).
    Layer 1: Callie calls this without confirmation -- the tool fires a background
    task and returns immediately. All writes stay within the workspace:marketing
    scope she already has full access to.
    """
    registry.register(DISPATCH_RESEARCH, _dispatch_research, layer=1)
