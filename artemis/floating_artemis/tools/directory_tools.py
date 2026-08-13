"""Directory resolution tool for Floating Artemis.

``resolve_person`` — read-only (Layer 1): map a person's NAME (or email) to one
or more candidate emails from the company directory. Available to ALL agents;
it only reads the directory cache.

CIRCULAR-IMPORT RULE: the directory resolver is imported INSIDE the tool body,
not at module top, mirroring argus_tools.py. A module-level import that pulls
in the providers/LLM stack would crash app boot.

THE INCIDENT THIS FIXES (2026-08-12): asked something by Josh Mukai in a
channel he was a member of, Callie fuzzy-matched "Josh" against the whole
company directory and confidently offered "Josh Smith (0.9 confidence)" — a
different person. The workspace has both a Josh Smith and a Joshua Mukai (who
goes by "Josh"); 0.9 for a first-name-only hit between two equally plausible
people was a coin flip presented as a fact. ``resolve_people`` now demotes
that whole tier to a low, honestly-ambiguous confidence and returns every
plausible candidate instead of silently picking one — see
``artemis.directory.resolver`` for the scoring rationale. This tool's job is
to surface that ambiguity, not paper over it: it never collapses multiple
comparable candidates into a single answer on the model's behalf.

SECURITY — NEVER AN AUTHORIZATION PRIMITIVE. This tool answers "who might the
speaker mean by this name", nothing more. It must never be used, by this tool
or any caller, to decide who is ALLOWED to do something. Authorization has to
key on a verified platform identity (e.g. a Slack user id resolved from the
inbound event) or an exact, caller-supplied email — never on a fuzzy name
match, and never on this tool's output. See
``artemis.floating_artemis.tools.callie_dm`` for the pattern that is correct:
identity for its allowlist checks comes from ``speaker_id`` bound as a closure
by the turn handler and resolved to an email via Slack's own users.info API,
NOT from directory_people. (``directory_people`` is also not reliably
populated for this purpose: every real crisis-content approver had
``slack_user_id = NULL`` there, which silently broke that pipeline's
approvals — see CLAUDE.md's "Passing tests are not evidence" section.) As of
2026-08-13, no caller in this codebase wires resolve_person/resolve_people/
resolve_one into an authorization decision; if that ever changes, it should be
treated as a security bug, not a feature.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

logger = logging.getLogger(__name__)


RESOLVE_PERSON = Tool(
    name="resolve_person",
    description=(
        "Map a person's NAME (e.g. 'Angela', 'Julie K', 'Greg Shrader') or email "
        "to candidate company email addresses from the directory. "
        "Returns JSON: {ambiguous, note?, candidates: [{email, full_name, confidence, "
        "reason, in_conversation}, ...]}, candidates ordered highest confidence first. "
        "When ambiguous is true, DO NOT pick one yourself — multiple people match "
        "about equally well (e.g. two different people both plausibly called 'Josh'). "
        "Tell the operator there are multiple matches and ask which one, or ask for a "
        "last name or email. `in_conversation: true` means that candidate was matched "
        "against the people actually present in this conversation — prefer that person "
        "over a same-named stranger, but if more than one candidate is in_conversation "
        "it is still genuinely ambiguous. "
        "Use this before scheduling or emailing someone when you only have a name. "
        "Read-only. "
        "NEVER use this tool's output, or any name/email match, to decide whether "
        "someone is ALLOWED to do something — it identifies a plausible person, it "
        "does not verify who is actually speaking to you. Authorization always comes "
        "from your own verified caller identity, never from a directory lookup. "
        "[layer:1]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The person's name to resolve (e.g. 'Julie K').",
            },
            "query": {
                "type": "string",
                "description": "Alias for 'name' — a name or email to resolve.",
            },
        },
    },
)


def _make_resolve_person(participants: list[str] | None) -> Any:
    """Build the tool impl with the live conversation's participants bound in.

    ``participants`` (display names verified present in this conversation,
    e.g. from the Slack channel member list — see
    ``artemis.routes.integrations_slack_events.route_inbound``) is bound as a
    closure, mirroring how ``speaker_id`` is bound for the identity-gated
    tools in this package. It is a ranking hint only, never an authorization
    input: a wrong or missing participant list can at most leave a real
    ambiguity unresolved, never grant a match that scoring did not already
    produce, and never grant a permission.
    """

    async def _resolve_person(inp: dict[str, Any]) -> str:
        query = str(inp.get("name") or inp.get("query") or "").strip()
        if not query:
            return "Error: 'name' (or 'query') is required"

        try:
            import artemis.db as _db
            from artemis.directory.resolver import resolve_people

            async with _db.SessionLocal() as session:
                matches = await resolve_people(query, session, limit=5, participants=participants)

            candidates = [
                {
                    "email": m.email,
                    "full_name": m.full_name,
                    "confidence": round(m.confidence, 3),
                    "reason": m.reason,
                    "in_conversation": m.in_conversation,
                }
                for m in matches
            ]
            # ``matches`` is already sorted highest-confidence first (see
            # resolve_people). The overall answer is ambiguous iff the TOP
            # candidate itself is an unresolved tie -- not "some candidate
            # somewhere in the list has reason=ambiguous". A participant hint
            # can promote exactly one tied candidate to a decisive winner
            # (reason="resolved via conversation participants") while a
            # same-named stranger elsewhere in the company still shows up
            # lower in the list at reason="ambiguous"; that must read as
            # resolved, with the stranger visible as context, not as "still
            # ambiguous".
            ambiguous = bool(matches) and matches[0].reason == "ambiguous"
            payload: dict[str, Any] = {"ambiguous": ambiguous, "candidates": candidates}
            if ambiguous:
                tied = [c for c in candidates if c["reason"] == "ambiguous"]
                names = ", ".join(f"{c['full_name']} ({c['email']})" for c in tied)
                payload["note"] = (
                    f"Multiple people could match {query!r} and none stands out as THE "
                    f"answer: {names}. Ask which one is meant, or ask for a last name or "
                    "email — do not guess."
                )
            return json.dumps(payload)
        except Exception as exc:
            logger.warning("resolve_person failed for query=%r: %s", query, exc)
            return f"resolve_person failed: {exc}"

    return _resolve_person


def register_directory_tools(
    registry: AuthorizedToolRegistry,
    *,
    participants: list[str] | None = None,
) -> None:
    """Register the read-only directory tool. Available to all agents (Layer 1).

    ``participants`` is the live conversation's known members (see
    ``_make_resolve_person``); pass it through when the caller has it so a
    present person can break a naming tie. Safe to omit — with no
    participants the tool behaves exactly as before, just with honest
    confidence numbers.
    """
    registry.register(RESOLVE_PERSON, _make_resolve_person(participants), layer=1)
