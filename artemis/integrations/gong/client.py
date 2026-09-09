"""Gong client that reads call METADATA and cannot read a transcript.

**There is deliberately no transcript method on this class.** Not a flag, not a
guarded path, not a method that raises: it does not exist. Jon's rule is that call
content is never stored, and the strongest form of that rule is a client that
cannot fetch the content in the first place. A prompt can be ignored and a flag
can be flipped by someone in a hurry; an absent method has to be written.

What it CAN read, and why that is enough:

- Call metadata: when, how long, how many people, which capture system.
- Salesforce context inline, via ``/v2/calls/extensive`` with
  ``contentSelector.context = "Extended"`` -- the Account (including
  ``District_Marketing_Tier__c``, so a notification can be tier-aware without a
  second Salesforce query) and related Opportunities with stage and
  days-since-stage-change.
- Trackers: 26 per call, as BARE COUNTS with no text attached. Gong's AI trackers
  return zero phrases even when occurrences are requested, so "Customer concerns
  fired 3 times" is the whole of what exists. That is the signal, and it carries
  no words.

Gong exposes no sentiment field anywhere, so trackers are the only affect signal
available. See ``docs/gong-capability-map.md``.

Two traps recorded there and honoured here:

**Pagination on /v2/calls/extensive.** The cursor goes at the TOP LEVEL of the
body, never inside ``filter``. Put it in ``filter`` and it is silently ignored,
returning page one forever with no error. This client batches ``filter.callIds``
instead, which sidesteps the question entirely.

**``affiliation`` has three values, not two.** ``Internal``, ``External`` and
``Unknown``, and on imported and Google Meet calls ``Unknown`` is the largest
group. Any logic treating "not Internal" as "the customer" is wrong on real data.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.gong.io"

#: Gong documents 3 requests/second and 10,000/day. The API returns only
#: ``x-ratelimit-remaining``, so the actual ceiling is not readable; this is a
#: deliberate under-run rather than a measured limit.
_MAX_IDS_PER_REQUEST = 100

#: Ceiling on pages walked for one lookup. 180 days is roughly 26 pages at 100
#: calls each; 40 leaves headroom without letting a bad filter walk the corpus.
_MAX_PAGES = 40


class GongUnavailableError(Exception):
    """Gong could not be reached or refused us. NOT "there were no calls".

    The distinction this codebase keeps relearning: an integration that returns
    an empty list when it is misconfigured reports a quiet week in exactly the
    words a working one uses.
    """


@dataclass
class CallContext:
    """One call, reduced to what can be said without quoting anyone."""

    call_id: str
    started: str | None
    duration_seconds: int | None
    title: str | None
    system: str | None
    account_name: str | None = None
    account_tier: str | None = None
    #: tracker name -> hit count. Counts only; Gong attaches no text to these.
    trackers: dict[str, int] = field(default_factory=dict)
    #: Counts only, never names. Who attended is not a fact about the district.
    internal_parties: int = 0
    external_parties: int = 0
    unknown_parties: int = 0
    opportunity_stage: str | None = None
    days_since_stage_change: int | None = None

    @property
    def fired_trackers(self) -> dict[str, int]:
        """Only the trackers that actually hit. All 26 return on every call."""
        return {name: n for name, n in self.trackers.items() if n}


class GongMetadataClient:
    """Read-only, metadata-only. Cannot fetch transcripts by construction."""

    def __init__(self, access_key: str, access_key_secret: str) -> None:
        self._key = access_key
        self._secret = access_key_secret

    def _headers(self) -> dict[str, str]:
        token = base64.b64encode(f"{self._key}:{self._secret}".encode()).decode()
        return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    def _require_credentials(self) -> None:
        if not self._key or not self._secret:
            raise GongUnavailableError(
                "GONG_ACCESS_KEY / GONG_ACCESS_KEY_SECRET are not set. This is NOT "
                "a report of zero calls -- say Gong is unavailable and do not "
                "state or imply anything about a district's recent contact."
            )

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self._require_credentials()
        try:
            async with httpx.AsyncClient(timeout=60, base_url=_BASE) as http:
                resp = await http.post(path, headers=self._headers(), json=body)
        except Exception as exc:
            logger.exception("gong: POST %s failed", path)
            raise GongUnavailableError(
                f"Gong {path} could not be reached ({type(exc).__name__}). This is "
                "not an empty result."
            ) from exc

        if resp.status_code >= 400:
            hint = "The credentials were rejected. " if resp.status_code in (401, 403) else ""
            if resp.status_code == 429:
                hint = "Rate limited (Gong allows ~3/sec, 10k/day). "
            logger.error("gong: POST %s -> HTTP %d", path, resp.status_code)
            raise GongUnavailableError(
                f"Gong {path} returned HTTP {resp.status_code}. {hint}This is not an empty result."
            )
        data: dict[str, Any] = resp.json()
        return data

    async def recent_calls_for_account(
        self, account_name: str, *, days: int = 180, limit: int = 5
    ) -> list[CallContext]:
        """Recent calls for one account, newest first, metadata only.

        Matches on the Salesforce Account name carried on the call itself, so no
        second Salesforce query is needed. Note that 2,582 calls imported from the
        previous vendor carry NO account link at all, so absence of results means
        "no linked call", never "no contact".
        """
        from datetime import UTC, datetime, timedelta

        frm = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
        to = datetime.now(UTC).strftime("%Y-%m-%dT23:59:59Z")

        wanted = account_name.strip().lower()
        matches: list[CallContext] = []
        cursor: str | None = None

        # The endpoint returns 100 calls a page, so a single request covers about
        # a fortnight of activity. Searching 180 days without paging silently
        # looked at the first page only and reported "no calls" for anything
        # older -- an under-report indistinguishable from a quiet account, which
        # is the exact failure shape this codebase keeps producing.
        #
        # The cursor goes at the TOP LEVEL of the body. Inside `filter` it is
        # accepted, ignored, and returns page one forever with no error.
        for _ in range(_MAX_PAGES):
            body: dict[str, Any] = {
                "filter": {"fromDateTime": frm, "toDateTime": to},
                "contentSelector": {
                    "context": "Extended",
                    "exposedFields": {
                        "parties": True,
                        "content": {"trackers": True},
                    },
                },
            }
            if cursor:
                body["cursor"] = cursor

            listing = await self._post("/v2/calls/extensive", body)
            for raw in listing.get("calls", []):
                ctx = _to_context(raw)
                if ctx.account_name and wanted in ctx.account_name.lower():
                    matches.append(ctx)

            cursor = (listing.get("records") or {}).get("cursor")
            if not cursor:
                break
        else:
            # Ran out of pages rather than out of data. Say so: silently
            # truncating is how a partial answer becomes a confident one.
            logger.warning(
                "gong: stopped after %d pages searching for %r; results may be partial",
                _MAX_PAGES,
                account_name,
            )

        matches.sort(key=lambda c: c.started or "", reverse=True)
        return matches[:limit]


def _to_context(raw: dict[str, Any]) -> CallContext:
    """Reduce one Gong call payload to the metadata-only shape.

    Everything dropped here is dropped on purpose. Party names and email
    addresses are present in the payload and are not carried forward: who
    attended is a fact about people, not about the district, and the scope is the
    district.
    """
    meta = raw.get("metaData") or {}
    ctx = raw.get("context") or []

    account_name = account_tier = stage = None
    days_since_stage: int | None = None
    for system in ctx:
        for obj in system.get("objects", []) or []:
            fields = {f.get("name"): f.get("value") for f in (obj.get("fields") or [])}
            if obj.get("objectType") == "Account":
                account_name = account_name or fields.get("Name")
                account_tier = account_tier or fields.get("District_Marketing_Tier__c")
            elif obj.get("objectType") == "Opportunity" and stage is None:
                stage = fields.get("StageName")
                raw_days = fields.get("DashboardsGSP__Days_Since_Last_Stage_Change__c")
                if isinstance(raw_days, int | float):
                    days_since_stage = int(raw_days)

    # Three values, not two -- Unknown is the largest group on imported calls.
    internal = external = unknown = 0
    for party in raw.get("parties") or []:
        affiliation = (party.get("affiliation") or "Unknown").strip().lower()
        if affiliation == "internal":
            internal += 1
        elif affiliation == "external":
            external += 1
        else:
            unknown += 1

    trackers: dict[str, int] = {}
    for tracker in (raw.get("content") or {}).get("trackers") or []:
        name = tracker.get("name")
        if name:
            trackers[str(name)] = int(tracker.get("count") or 0)

    return CallContext(
        call_id=str(meta.get("id") or ""),
        started=meta.get("started"),
        duration_seconds=meta.get("duration"),
        title=meta.get("title"),
        system=meta.get("system"),
        account_name=account_name,
        account_tier=account_tier,
        trackers=trackers,
        internal_parties=internal,
        external_parties=external,
        unknown_parties=unknown,
        opportunity_stage=stage,
        days_since_stage_change=days_since_stage,
    )


async def recent_contact_summary(account_name: str, *, days: int = 180) -> str | None:
    """One paragraph of conversation CONTEXT for a district, or None.

    **Why this is a function and not just a tool.** The value is not answering
    "what's the context on X" when someone asks. It is that Callie was drafting
    COLD outreach for Grosse Pointe while two calls sat in Gong from 1 and 11 May,
    one of them firing an Objections tracker. Nobody would have thought to ask.
    So this is folded into `check_salesforce_activity`, which she is already
    required to call before drafting outreach, rather than added as a tool she
    must remember.

    Derived facts only: when, how long, roughly what it concerned via tracker
    names, and the Salesforce stage. Never a word anyone said. Participant names
    are dropped; counts are all that survive.
    """
    from artemis.config import settings

    client = GongMetadataClient(settings.gong_access_key, settings.gong_access_key_secret)
    try:
        calls = await client.recent_calls_for_account(account_name, days=days, limit=5)
    except GongUnavailableError as exc:
        # Distinct from "no calls". Reported so it can be repeated verbatim
        # rather than mistaken for a quiet account.
        logger.warning("gong: context lookup failed for %r: %s", account_name, exc)
        return (
            "Gong: could not be reached, so recent-conversation context is UNKNOWN "
            "for this district. That is not the same as no contact -- do not "
            "describe this account as cold."
        )

    if not calls:
        # Deliberately not "never contacted": 2,582 imported calls carry no
        # account link at all, so absence of a match is absence of a LINK.
        return (
            "Gong: no linked calls in the last "
            f"{days} days. Note that calls imported from the previous vendor "
            "carry no account link, so this means no linked call rather than no "
            "contact."
        )

    newest = calls[0]
    lines = [f"Gong: {len(calls)} linked call(s) in the last {days} days."]
    for call in calls:
        when = str(call.started or "")[:10]
        minutes = round((call.duration_seconds or 0) / 60)
        fired = ", ".join(sorted(call.fired_trackers)) or "no trackers fired"
        lines.append(f"   {when} — {minutes} min, {call.external_parties} external — {fired}")

    if newest.opportunity_stage:
        stalled = (
            f", {newest.days_since_stage_change}d in stage"
            if newest.days_since_stage_change is not None
            else ""
        )
        lines.append(f"   Opportunity: {newest.opportunity_stage}{stalled}")
    if newest.account_tier:
        lines.append(f"   District tier: {newest.account_tier}")

    lines.append(
        "   This district has been spoken to. Do NOT write to them as a cold "
        "account, and say what the last contact was rather than opening as if "
        "there was none. Tracker names indicate what a call touched on; they are "
        "counts, not quotes, so do not claim to know what anyone said."
    )
    return "\n".join(lines)


async def one_line_contact(account_name: str, *, days: int = 180) -> str | None:
    """A single line of prior-contact context, or None when there is nothing to say.

    The compact form of `recent_contact_summary`, for places where a paragraph
    would drown the thing it is annotating: a signal push, a brief entry, a
    worklist row.

    Returns None on "no linked calls" and on a Gong outage. That is deliberate
    and it is the opposite of the full summary's behaviour: here the line is an
    ANNOTATION on someone else's message, so silence just means no annotation. In
    the full summary the same states must be stated out loud, because there the
    absence of a Gong section would read as "no prior contact".
    """
    from artemis.config import settings

    if not settings.gong_access_key or not settings.gong_access_key_secret:
        return None

    client = GongMetadataClient(settings.gong_access_key, settings.gong_access_key_secret)
    try:
        calls = await client.recent_calls_for_account(account_name, days=days, limit=5)
    except GongUnavailableError:
        logger.warning("gong: one-line context unavailable for %r", account_name)
        return None
    if not calls:
        return None

    newest = calls[0]
    when = str(newest.started or "")[:10]
    fired = sorted({t for call in calls for t in call.fired_trackers})
    themes = f", touching on {', '.join(fired[:3])}" if fired else ""
    plural = "s" if len(calls) != 1 else ""
    return (
        f"{len(calls)} call{plural} in the last {days} days, most recent {when}"
        f"{themes}. Not a cold account."
    )
