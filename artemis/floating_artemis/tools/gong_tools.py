"""What districts are saying, as something Callie can be asked (GONG-1).

One tool, layer 1 (read-only): ``district_call_signal``. Until now the tracker
signal existed in exactly one place -- a line in the daily brief -- so "which
districts are saying good things about us?" had no answer and "what is going on
with Pinellas?" had no answer either. A signal with one consumer, pushed once a
day, is a signal nobody can follow up on.

**Rule 4 lives here as much as anywhere.** Nothing this returns is a word anyone
said, because nothing it reads is: trackers are integers attached to an account,
and the client underneath has no transcript method. Nothing is keyed to a rep,
ever -- who was on a call is not a fact about the district, and the credential
being able to read all 22 reps' conversations is exactly why that restraint has
to be in the code rather than in the prompt.

**Written against hallucination, not just for retrieval.** The failure this tool
would otherwise invite is a confident summary of a district we know nothing
about. So every branch that could be mistaken for good news says what it is:
Gong unreachable is UNKNOWN and not "quiet", a name that matches nothing lists
the near misses rather than picking one, too few calls is "not a finding either
way", and a matched district with no signal is "nothing unusual" and explicitly
not "no problems". Every path carries the counts-not-quotes caveat, so the model
repeats it rather than inventing a basis it does not have.

Registered ONLY inside
``artemis.floating_artemis.tool_registry._build_callie_tool_registry``. No other
agent gets it unless a future brief adds it there explicitly.
"""

from __future__ import annotations

import logging
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

logger = logging.getLogger(__name__)

DISTRICT_CALL_SIGNAL = "district_call_signal"

#: Repeated on every path on purpose. It is the sentence that stops "Product
#: feedback on 100% of calls" being read back to Josh as "they complained".
_CAVEAT = (
    "These are counts of what a call touched on, from Gong's own trackers. They "
    "never say what anyone said, and there is no transcript behind them."
)

#: A shortlist is a thing someone reads top-down. A ranked list of forty is not.
_MAX_LISTED = 8


async def _from_stored(kind: str, session_factory: Any = None) -> str:
    """The portfolio question, answered from stored readings with no API call.

    The daily section already wrote these. Re-surveying 800 calls to answer
    "who is positive at the moment" would cost eight requests and several
    seconds for an answer that was computed this morning.
    """
    import artemis.db as _db
    from artemis.integrations.gong.snapshots import previous_readings

    factory = session_factory or _db.SessionLocal
    async with factory() as session:
        # min_age_days=0: here we want the CURRENT picture, not something old
        # enough to trend against.
        readings = await previous_readings(session, min_age_days=0)

    if not readings:
        # Distinguishable from "nobody is saying anything", which is what an
        # empty list would be read as.
        return (
            "No stored call readings. That means the daily market-signals brief has "
            "not recorded any yet -- NOT that no district has a signal. Ask about a "
            "specific district by name and I will read Gong directly."
        )

    as_of = max(r.on for r in readings.values())
    concern = sorted(
        (r for r in readings.values() if r.concern),
        key=lambda r: (-max(r.concern.values()), -r.calls),
    )
    positive = sorted(
        (r for r in readings.values() if r.advocacy),
        key=lambda r: (-max(r.advocacy.values()), -r.calls),
    )

    lines = [f"Stored call readings as of {as_of:%d %b %Y}, across {len(readings)} districts."]
    if kind in ("any", "positive") and positive:
        lines.append("")
        lines.append("Districts sounding positive (case-study leads):")
        for r in positive[:_MAX_LISTED]:
            top, rate = max(r.advocacy.items(), key=lambda kv: kv[1])
            lines.append(f"  {r.account_name} — {top} on {rate:.0%} of {r.calls} calls")
    if kind in ("any", "concern") and concern:
        lines.append("")
        lines.append("Districts raising more than usual:")
        for r in concern[:_MAX_LISTED]:
            top, rate = max(r.concern.items(), key=lambda kv: kv[1])
            lines.append(f"  {r.account_name} — {top} on {rate:.0%} of {r.calls} calls")
    if len(lines) == 1:
        lines.append(f"None of the {len(readings)} stored readings are of that kind.")

    lines.append("")
    lines.append(
        "Only districts WITH a signal are stored, so a district absent from this list "
        "either looked like everyone else or had too few calls to judge. " + _CAVEAT
    )
    # Deliberately not silent about staleness: the readings are from the last
    # brief run, and a district can have moved since.
    lines.append(
        f"Ask about one by name for a fresh read -- these are as of {as_of:%d %b} and "
        "are not recomputed by this answer."
    )
    return "\n".join(lines)


async def _for_district(name: str, session_factory: Any = None) -> str:
    """One district, read live, because a stored reading only exists if it was flagged."""
    import artemis.db as _db
    from artemis.integrations.gong.snapshots import previous_readings, trend_for
    from artemis.integrations.gong.survey import LOOKBACK_DAYS, run_survey

    try:
        survey = await run_survey()
    except Exception:
        logger.warning("district_call_signal: survey failed for %r", name, exc_info=True)
        return (
            f"Gong could not be reached, so the call signal for {name!r} is UNKNOWN. "
            "That is not the same as a quiet district -- do not describe it as having "
            "no concerns on the strength of this."
        )

    dev = survey.for_account(name)
    if dev is None:
        near = survey.candidates(name)
        if near:
            # Say when the list is cut. Six names with no "and 9 more" reads as
            # the complete set, and the reader picks from a set that is not it.
            listed = ", ".join(near[:6])
            if len(near) > 6:
                listed += f", and {len(near) - 6} more"
            return (
                f"{name!r} matches more than one account in Gong: {listed}. Tell me which "
                "one and I will read it -- I am not going to pick, because attributing "
                "another district's calls to this one is the error that matters here."
            )
        return (
            f"No linked Gong calls for {name!r} in the last {LOOKBACK_DAYS} days, out of "
            f"{survey.portfolio.call_count} linked calls in that window. Note that calls imported from the previous vendor carry no account "
            "link at all, so this means no LINKED call rather than no contact -- and "
            "the name may simply differ from the Salesforce account name Gong records."
        )

    lines = [dev.describe()]

    if dev.has_signal:
        factory = session_factory or _db.SessionLocal
        try:
            async with factory() as session:
                previous = await previous_readings(session)
            verdict = trend_for(dev, previous.get(dev.account_name))
            if verdict.direction in ("worsening", "improving"):
                lines.append(f"  Trend: {verdict.describe()}")
        except Exception:
            logger.warning("district_call_signal: trend lookup failed", exc_info=True)

    if survey.truncated:
        lines.append(
            "  Caution: the call window was truncated before the data ran out, so these "
            "counts are a floor rather than a total."
        )
    if not dev.has_signal and not dev.insufficient:
        lines.append(
            "  'Nothing unusual' means their tracker rates look like everyone else's. "
            "It is not a clean bill of health."
        )
    # `describe()` carries its own version of this for a district with a signal.
    # Saying it twice in one answer reads as boilerplate, and boilerplate is what
    # a model learns to skip.
    if "Tracker counts only" not in lines[0]:
        lines.append(f"  {_CAVEAT}")
    return "\n".join(lines)


async def _district_call_signal(inp: dict[str, Any], *, session_factory: Any = None) -> str:
    district_name = str(inp.get("district_name") or "").strip()
    kind = str(inp.get("kind") or "any").strip().lower()
    if kind not in ("any", "concern", "positive"):
        kind = "any"

    if district_name:
        return await _for_district(district_name, session_factory)
    return await _from_stored(kind, session_factory)


def register_gong_tools(registry: AuthorizedToolRegistry) -> None:
    registry.register(
        Tool(
            name=DISTRICT_CALL_SIGNAL,
            description=(
                "What districts are saying, from Gong call trackers. With no arguments: "
                "the current shortlist of districts sounding POSITIVE (case-study leads) "
                "and districts raising concerns more than usual. With district_name: a "
                "fresh read on that one district, including whether it is getting better "
                "or worse. Counts only -- it can never tell you what anyone said, and it "
                "reports 'unknown' rather than 'quiet' when Gong cannot be reached. "
                "District-level only; it holds nothing about individual reps."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "district_name": {
                        "type": "string",
                        "description": (
                            "One district to read live. Omit for the portfolio shortlist."
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["any", "concern", "positive"],
                        "description": "Which half of the shortlist. Ignored with district_name.",
                    },
                },
            },
        ),
        _district_call_signal,
        layer=1,
    )
