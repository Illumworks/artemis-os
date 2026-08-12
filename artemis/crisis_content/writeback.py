"""Write the decision back to Jen's doc + notify her (slice C, CCA7).

Background: ``docs/crisis-content-approval-pipeline.md`` and
``briefs/cca7-writeback-and-notify-jen.md``. Every prior slice was read-only
against Jen's Google Doc (an external vendor's live crisis-communications
document, edited by people while this runs). This module is the first one
that writes to it, so the non-negotiables from the brief's safety section are
load-bearing here, not decoration:

1. **Insert only.** ``_insert_text`` issues exactly one ``insertText``
   request per call and nothing else. There is no ``deleteContentRange`` or
   any content-replacing request anywhere in this module -- do not add one.
2. **Never write to a card that has not been positively identified.**
   ``locate_card_table`` raises ``CardNotLocatedError`` on anything but
   exactly one match; every caller treats that as "log ERROR, alert Jon,
   write nothing" and NEVER falls back to a best guess (e.g. table index,
   first match, most-recent match).
3. **Verify after writing.** ``write_doc_line`` re-fetches the doc after the
   insert and confirms the card count is unchanged and the inserted line is
   present in the same table. A mismatch raises
   ``WritebackVerificationError``, which is treated as a loud, standing
   alarm -- NOT as a trigger for a cleanup write (a second write attempting
   to fix a possibly-already-damaged document is exactly the compounding
   mistake the brief warns against).

``write_doc_line`` is public (not the usual leading-underscore private
helper) because ``artemis.crisis_content.image_link`` (CCA10) reuses it
verbatim -- CCA10's brief explicitly says to reuse this module's
``locate_card_table`` and index logic rather than re-deriving it, and this
function's contract (header + copy_hash -> locate, insert one line, verify)
has nothing decision-specific in it. It is the ONLY function promoted this
way; every other private helper in this module stays private, and CCA10
writes its own independent copy of the credential-resolution machinery
(matching the established "independent copy per module, deliberately" style
already used by ``poller.py`` and this module for that specific piece).

Why "header + platform" (the brief's stated card-matching signature) is not
what this module actually matches on: the Docs API's ``documents.get`` is
just as opaque to the Platform dropdown chip as it is to the two status
chips (see ``docs/crisis-content-approval-pipeline.md`` finding 1 -- chips
are opaque to the Docs API in BOTH directions, not just for the two status
fields). A chip renders as an empty structural range with no content under
``includeTabsContent=true`` exactly as it does under a plain fetch, so
``^Platform:\\s*(.+)$`` never matches anything in the JSON body -- there is
no live platform value to compare against here. This module matches on
(signature ∧ header text ∧ copy-body hash) instead: the header cell and the
copy cell are both literal text (no chips), and ``CrisisContentCard.copy_hash``
is refreshed on every ~2-minute poll, so it is a live, comparably-fresh
disambiguator when two cards share a header (see the design doc's own
"Primary key / secondary guard" idea, adapted here because the primary key's
platform component cannot be read back through this API at all). See the
docstring on ``locate_card_table`` and the CCA7 write-up's "brief issues"
section for the full reasoning -- this is flagged as a probable error in the
brief, not silently worked around.

Idempotency: one row per ``(decision_id, action)`` in
``crisis_content_writeback_deliveries`` (see the migration and
``CrisisContentWritebackDelivery`` below), action in
``{"doc_line", "comment", "email"}``, checked and written independently for
each action -- a failure on the email must never cause the doc line to be
re-inserted on retry, and vice versa. A row is written ONLY after the
corresponding side effect has actually succeeded, mirroring
``crisis_content_notifications``' "mark only after a successful post"
discipline (``artemis.crisis_content.transitions.mark_notified``).

The Drive comment and the Gmail backup do NOT depend on locating the card
inside the live document structure -- they only need the DB-known card
(header/title/platform, already resolved from ``decision.card_id``) plus the
decision itself, neither of which requires parsing the live Docs API JSON.
So when the doc line cannot be safely written (ambiguous or missing target),
the comment and email still go out: Jen still needs to hear about the
decision, and the belt-and-braces framing in the brief (the email exists
specifically for when the primary notification is missed) argues for
notifying MORE, not less, when the riskiest step is the one that had to be
skipped. This is a judgment call where the brief's "nothing written" wording
is ambiguous between "nothing at all happens" and "no text lands in the
document" -- documented here and in the CCA7 report rather than guessed at
silently.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from sqlalchemy import BigInteger, ForeignKey, Text, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

import artemis.db as _db
from artemis.config import settings
from artemis.crisis_content.export_client import TARGET_DOCUMENT_ID
from artemis.crisis_content.orm import CrisisContentCard, CrisisContentDecision
from artemis.db import Base
from artemis.google_docs.client import GoogleReauthRequiredError, refresh_access_token
from artemis.google_docs.models import GoogleCredential
from artemis.google_integration import google_has_any_scope, resolve_google_oauth_client_config
from artemis.integrations.gmail.client import GmailClient
from artemis.integrations.slack.client import SlackClient
from artemis.proactivity.commitments import (
    _get_slack_token_for_agent,
    _resolve_artemis_dm_recipient,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CrisisContentWritebackDelivery",
    "CardNotLocatedError",
    "WritebackVerificationError",
    "WritebackOutcome",
    "deliver_decision_writeback",
    "schedule_decision_writeback",
    "render_writeback_line",
    "locate_card_table",
    "write_doc_line",
]

_DOCS_API_BASE = "https://docs.googleapis.com/v1"
_DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
_DOC_URL = f"https://docs.google.com/document/d/{TARGET_DOCUMENT_ID}/edit"

_DELIVERY_CONSTRAINT = "uq_crisis_content_writeback_decision_action"

DeliveryAction = Literal["doc_line", "comment", "email"]
DeliveryStatus = Literal[
    "delivered",
    "already_delivered",
    "not_located",
    "damaged",
    "failed",
    "disabled",
    # A vendor-facing delivery deliberately not made because the card lives on
    # the TESTING tab (CCA13 + migration 0111). Distinct from "disabled" (the
    # kill switch is off) and from "failed" (we tried and could not) -- this one
    # means we chose not to, and nothing is wrong.
    "skipped_test_card",
]


def jen_emails() -> tuple[str, ...]:
    """The addresses to @mention/email, from settings (not an inline literal).

    Both of Jen's addresses -- ``jen@justrightstrategy.com`` (the doc owner)
    and ``jen@digigeeks.com`` (a writer) -- are notified by default; see the
    ``crisis_content_writeback_jen_emails`` setting.
    """
    raw = settings.crisis_content_writeback_jen_emails
    return tuple(part.strip() for part in raw.split(",") if part.strip())


class CrisisContentWritebackDelivery(Base):
    """Idempotency ledger -- one row per ``(decision_id, action)`` delivered.

    See ``alembic/versions/0108_crisis_content_writeback_deliveries.py`` for
    the migration and the module docstring above for the "check + write
    independently, per action" contract this table exists to support.
    """

    __tablename__ = "crisis_content_writeback_deliveries"
    __table_args__ = (UniqueConstraint("decision_id", "action", name=_DELIVERY_CONSTRAINT),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    decision_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "crisis_content_decisions.id",
            name="fk_crisis_content_writeback_decision",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class CardNotLocatedError(Exception):
    """The target card could not be positively identified in the live doc.

    Callers MUST treat this as "log ERROR, alert Jon, write nothing" -- never
    fall back to a best guess (table index, first match, most recent tab).
    """


class WritebackVerificationError(Exception):
    """Post-write verification found a changed card count or a missing line.

    Raised AFTER the ``insertText`` call has already succeeded -- the write
    already happened. Callers must alert loudly and must NOT attempt a
    cleanup write in response (see the module docstring and the brief's
    "do not attempt a cleanup write that could compound the damage").
    """


class _CredentialUnavailableError(Exception):
    """The personal Google credential is missing, expired, or under-scoped."""


@dataclass(frozen=True)
class WritebackOutcome:
    """What happened for each of the three actions on one decision."""

    doc_line: DeliveryStatus
    comment: DeliveryStatus
    email: DeliveryStatus


# ---------------------------------------------------------------------------
# Docs API JSON helpers -- pure functions over the includeTabsContent=true
# response shape. See the module docstring for why platform cannot be read
# here (chip opacity) and why header + copy-body hash is used instead.
# ---------------------------------------------------------------------------

_SIGNATURE_MARKERS = ("Platform:", "Copy review")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_ws(text: str) -> str:
    """Collapse whitespace (including a decoded &nbsp;) to single spaces, and strip.

    Deliberately a local copy of ``artemis.crisis_content.parser._normalize_ws``
    rather than an import of that private helper -- this module reads the
    Docs API's structured JSON body, not the HTML export ``parser.py`` reads,
    so the two are siblings with the same small utility, not a shared
    dependency. Same reasoning as the two independent card-id lookups in
    ``poller.py`` / ``transitions.py``.
    """
    return _WHITESPACE_RE.sub(" ", text.replace("\xa0", " ")).strip()


def _iter_tabs(tabs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten ``tabs`` + every nested ``childTabs`` into one document-order list."""
    flattened: list[dict[str, Any]] = []
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        flattened.append(tab)
        child_tabs = tab.get("childTabs")
        if isinstance(child_tabs, list):
            flattened.extend(_iter_tabs(child_tabs))
    return flattened


def _cell_text(cell: dict[str, Any]) -> str:
    """Concatenate all text runs in a table cell, in document order."""
    parts: list[str] = []
    for element in cell.get("content") or []:
        if not isinstance(element, dict):
            continue
        paragraph = element.get("paragraph")
        if not isinstance(paragraph, dict):
            continue
        for pe in paragraph.get("elements") or []:
            if not isinstance(pe, dict):
                continue
            text_run = pe.get("textRun")
            if isinstance(text_run, dict):
                content = text_run.get("content")
                if isinstance(content, str):
                    parts.append(content)
    return "".join(parts)


def _cell_paragraph_lines(cell: dict[str, Any]) -> list[str]:
    """Normalized text of each paragraph directly in ``cell``, one per line.

    Mirrors ``parser._paragraph_lines``' "one <p> = one line" rule, applied
    to the JSON body's paragraph structural elements instead of HTML <p>
    tags -- this is what makes ``_copy_hash_for_table`` below comparable to
    ``CrisisContentCard.copy_hash`` (computed by ``parser._build_card`` the
    same way, over the same line-joining rule).
    """
    lines: list[str] = []
    for element in cell.get("content") or []:
        if not isinstance(element, dict):
            continue
        paragraph = element.get("paragraph")
        if not isinstance(paragraph, dict):
            continue
        parts: list[str] = []
        for pe in paragraph.get("elements") or []:
            if not isinstance(pe, dict):
                continue
            text_run = pe.get("textRun")
            if isinstance(text_run, dict):
                content = text_run.get("content")
                if isinstance(content, str):
                    parts.append(content)
        lines.append(_normalize_ws("".join(parts)))
    return lines


def _table_text(table: dict[str, Any]) -> str:
    parts: list[str] = []
    for row in table.get("tableRows") or []:
        if not isinstance(row, dict):
            continue
        for cell in row.get("tableCells") or []:
            if isinstance(cell, dict):
                parts.append(_cell_text(cell))
    return "".join(parts)


def _is_review_card_table(table: dict[str, Any]) -> bool:
    """Same signature the parser uses: a table containing both markers."""
    text = _table_text(table)
    return all(marker in text for marker in _SIGNATURE_MARKERS)


def _header_cell(table: dict[str, Any]) -> dict[str, Any] | None:
    rows = table.get("tableRows") or []
    if not rows or not isinstance(rows[0], dict):
        return None
    cells = rows[0].get("tableCells") or []
    if not cells or not isinstance(cells[0], dict):
        return None
    return cells[0]


def _status_cell(table: dict[str, Any]) -> dict[str, Any] | None:
    rows = table.get("tableRows") or []
    if len(rows) < 2 or not isinstance(rows[1], dict):
        return None
    cells = rows[1].get("tableCells") or []
    if not cells or not isinstance(cells[0], dict):
        return None
    return cells[0]


def _copy_cell(table: dict[str, Any]) -> dict[str, Any] | None:
    rows = table.get("tableRows") or []
    if len(rows) < 2 or not isinstance(rows[1], dict):
        return None
    cells = rows[1].get("tableCells") or []
    if len(cells) < 2 or not isinstance(cells[1], dict):
        return None
    return cells[1]


def _header_text(table: dict[str, Any]) -> str | None:
    cell = _header_cell(table)
    if cell is None:
        return None
    return _normalize_ws(_cell_text(cell))


def _copy_hash_for_table(table: dict[str, Any]) -> str | None:
    cell = _copy_cell(table)
    if cell is None:
        return None
    lines = _cell_paragraph_lines(cell)
    copy_body = "\n".join(line for line in lines if line)
    return hashlib.sha256(copy_body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _CardLocation:
    tab_id: str
    table: dict[str, Any]


def _find_all_card_tables(document: dict[str, Any]) -> list[_CardLocation]:
    """Every signature-matching table in the doc, tagged with its owning tab.

    Tab-agnostic on purpose, same as the read-path parser: walks every tab
    (recursing through ``childTabs``) rather than resolving a specific tab
    id, so a new monthly tab just contributes more candidates.
    """
    locations: list[_CardLocation] = []
    raw_tabs = document.get("tabs")
    if not isinstance(raw_tabs, list):
        return locations
    for tab in _iter_tabs(raw_tabs):
        tab_properties = tab.get("tabProperties")
        tab_id = tab_properties.get("tabId") if isinstance(tab_properties, dict) else None
        if not tab_id:
            continue
        document_tab = tab.get("documentTab")
        body = document_tab.get("body") if isinstance(document_tab, dict) else None
        content = body.get("content") if isinstance(body, dict) else None
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            table = item.get("table")
            if isinstance(table, dict) and _is_review_card_table(table):
                locations.append(_CardLocation(tab_id=str(tab_id), table=table))
    return locations


def locate_card_table(
    document: dict[str, Any], *, header: str, copy_hash: str
) -> tuple[_CardLocation, int]:
    """Positively identify the ONE live table matching this card, or raise.

    Returns ``(location, total_card_table_count)`` -- the count is every
    signature-matching table in the whole document, used as the "card count
    unchanged" baseline by the post-write verification.

    Matching strategy (see the module docstring for why platform is not
    part of it): first narrow to tables whose header text matches exactly.
    If that alone is unambiguous (0 or 1 matches other than the found one),
    done. If MORE than one live table shares the header (the "August XX"
    placeholder collision the design doc calls out), narrow further by
    comparing each candidate's live copy-cell hash against ``copy_hash``
    (refreshed on every ~2 minute poll, so it is a fresh, comparable
    signal). Anything other than exactly one surviving candidate at either
    stage raises ``CardNotLocatedError`` -- there is no third disambiguator
    and no best-guess fallback.
    """
    all_cards = _find_all_card_tables(document)
    normalized_header = _normalize_ws(header)
    header_matches = [loc for loc in all_cards if _header_text(loc.table) == normalized_header]

    if not header_matches:
        raise CardNotLocatedError(
            f"No live table matches header={header!r} -- {len(all_cards)} review-card "
            "table(s) found in the doc, none with this header. It may have been "
            "renamed, moved, or removed since the last poll."
        )

    if len(header_matches) == 1:
        return header_matches[0], len(all_cards)

    hash_matches = [loc for loc in header_matches if _copy_hash_for_table(loc.table) == copy_hash]
    if len(hash_matches) == 1:
        return hash_matches[0], len(all_cards)

    raise CardNotLocatedError(
        f"{len(header_matches)} live table(s) share header={header!r}, and "
        f"{len(hash_matches)} of those also match the last-known copy hash -- "
        "cannot positively identify the target card. Refusing to write."
    )


def _append_index_for_status_cell(status_cell: dict[str, Any]) -> int:
    """Index just before the status cell's trailing newline -- an append point.

    Inserting ``"\\n" + text`` here creates a new paragraph after every
    existing line in the cell while leaving the cell's own closing boundary
    (and everything after it, including the adjacent copy cell) untouched --
    the standard Docs API "append a paragraph inside this container" idiom.
    """
    content = status_cell.get("content") or []
    if not content:
        raise CardNotLocatedError("Status cell has no content -- cannot compute an insert index")
    last = content[-1]
    if not isinstance(last, dict):
        raise CardNotLocatedError("Status cell's last element is malformed")
    end_index = last.get("endIndex")
    if not isinstance(end_index, int):
        raise CardNotLocatedError(
            "Status cell's last element has no endIndex -- refusing to insert"
        )
    return end_index - 1


# ---------------------------------------------------------------------------
# Line / message rendering
# ---------------------------------------------------------------------------

_ROUTE_LABELS = {"asset": "Asset", "copy": "Copy"}


def render_writeback_line(
    *,
    route: str,
    decision: str,
    actor_label: str,
    decided_at: datetime,
    note: str | None,
) -> str:
    """The line inserted into the card after the status block.

    Keeps the brief's literal ``"Approved — ..."`` / ``"Changes requested —
    ..."`` wording, with the ROUTE added as a parenthetical qualifier
    (``"Approved (copy) — ..."``) rather than folded into the verb: a single
    card carries two INDEPENDENT decisions (asset + copy, see
    ``docs/crisis-content-approval-pipeline.md`` "Routing"), so two lines on
    the same card without a route marker would be indistinguishable. Flagged
    as a deliberate addition beyond the brief's literal example, not an
    oversight.

    Always ends with the "chip is Jen's to flip" caveat (brief constraint
    #1) -- the two sources of truth (this line and her dropdown) must never
    silently disagree.
    """
    route_label = _ROUTE_LABELS.get(route, route.capitalize()).lower()
    stamp = (
        f"{decided_at.strftime('%b')} {decided_at.day}, {decided_at.strftime('%-I:%M%p').lower()}"
    )
    if decision == "approved":
        head = f"✅ Approved ({route_label}) — {actor_label}, {stamp}"
    else:
        head = f"✏️ Changes requested ({route_label}) — {actor_label}, {stamp}"
        if note:
            head = f"{head}: {note}"
    return (
        f"{head}. (Recorded by Artemis — the chip above is Jen's to flip by hand; "
        "it does not update automatically.)"
    )


def _actor_label(decision: CrisisContentDecision) -> str:
    """Best-effort human label -- mirrors ``slack_actions._display_label``."""
    if decision.decided_by_email:
        return decision.decided_by_email
    if decision.decided_by_slack_user_id:
        return f"<@{decision.decided_by_slack_user_id}>"
    return "unknown"


def _decision_summary(*, card: CrisisContentCard, decision: CrisisContentDecision) -> str:
    verb = "Approved" if decision.decision == "approved" else "Changes requested"
    route_label = _ROUTE_LABELS.get(decision.route, decision.route.capitalize())
    platform = card.identity_platform or "unspecified platform"
    title = card.title or card.identity_header
    return f'{verb} ({route_label} route) — {_actor_label(decision)} on "{title}" ({platform})'


def _comment_content(*, card: CrisisContentCard, decision: CrisisContentDecision) -> str:
    lines = [_decision_summary(card=card, decision=decision) + "."]
    if decision.note:
        lines.append(f"Note: {decision.note}")
    lines.append(
        "Recorded as a line on the card in the doc. The chip above it is still "
        "yours to flip by hand -- it doesn't update automatically."
    )
    lines.append(" ".join(f"+{email}" for email in jen_emails()))
    return "\n".join(lines)


def _email_content(*, card: CrisisContentCard, decision: CrisisContentDecision) -> tuple[str, str]:
    title = card.title or card.identity_header
    verb = "Approved" if decision.decision == "approved" else "Changes requested"
    subject = f"[Amira crisis content] {verb}: {title}"
    lines = [_decision_summary(card=card, decision=decision) + "."]
    lines.append("")
    if decision.note:
        lines.append(f"Note: {decision.note}")
        lines.append("")
    lines.append(
        "This has been recorded as a line on the card in the doc. The relevant "
        "status chip still reads whatever it last did -- it does not update "
        "automatically, so please flip it by hand when you get a chance."
    )
    lines.append("")
    lines.append(f"Doc: {_DOC_URL}")
    return subject, "\n".join(lines)


# ---------------------------------------------------------------------------
# Google credential resolution -- BY PURPOSE, never a hardcoded user id.
# See artemis/proactivity/agency_gate.py::_resolve_personal_gmail_client and
# artemis/crisis_content/poller.py::_resolve_access_token for the two prior
# fixes of this exact mistake (user_id=1 is the dev@local shim, not Jon).
# This is a third, independent copy for this module's own failure contract
# (raises _CredentialUnavailableError, not an HTTPException -- there is no
# request here), same pattern as poller.py's own docstring explains for why
# it doesn't import agency_gate's private helper either.
# ---------------------------------------------------------------------------


async def _resolve_personal_credential(session: AsyncSession) -> GoogleCredential:
    result = await session.execute(
        select(GoogleCredential)
        .where(GoogleCredential.purpose == "personal")
        .order_by(GoogleCredential.updated_at.desc())
        .limit(1)
    )
    credential = result.scalar_one_or_none()
    if credential is None:
        raise _CredentialUnavailableError(
            "No personal Google credential connected -- connect via "
            "/api/google/oauth/start?purpose=personal"
        )

    now = datetime.now(UTC)
    if credential.expiry > now + timedelta(seconds=60):
        return credential

    if not credential.refresh_token:
        raise _CredentialUnavailableError(
            "Personal Google credential has no refresh_token -- reconnect required"
        )

    client_config = await resolve_google_oauth_client_config(session)
    try:
        refreshed = await refresh_access_token(
            refresh_token=credential.refresh_token,
            client_id=client_config.client_id,
            client_secret=client_config.client_secret,
        )
    except GoogleReauthRequiredError as exc:
        raise _CredentialUnavailableError(f"Google reconnect required: {exc}") from exc
    except httpx.HTTPError as exc:
        raise _CredentialUnavailableError(f"Google token refresh failed: {exc}") from exc

    credential.access_token = refreshed.access_token
    credential.refresh_token = refreshed.refresh_token
    credential.expiry = refreshed.expiry
    if refreshed.scope:
        credential.scope = refreshed.scope
    credential.updated_at = now
    await session.flush()
    return credential


async def _resolve_docs_access_token(session: AsyncSession) -> str:
    credential = await _resolve_personal_credential(session)
    return credential.access_token


async def _resolve_personal_gmail_client(session: AsyncSession) -> GmailClient:
    credential = await _resolve_personal_credential(session)
    if not google_has_any_scope(credential.scope, "https://www.googleapis.com/auth/gmail.send"):
        raise _CredentialUnavailableError(
            "Personal Google credential is missing gmail.send scope -- reconnect required"
        )
    client_config = await resolve_google_oauth_client_config(session)

    async def _on_tokens_refreshed(
        new_access_token: str, new_refresh_token: str, new_expires_at: float
    ) -> None:
        credential.access_token = new_access_token
        if new_refresh_token:
            credential.refresh_token = new_refresh_token
        credential.expiry = datetime.fromtimestamp(new_expires_at, tz=UTC)
        credential.updated_at = datetime.now(UTC)

    return GmailClient(
        access_token=credential.access_token,
        refresh_token=credential.refresh_token or "",
        client_id=client_config.client_id,
        client_secret=client_config.client_secret,
        expires_at=credential.expiry.timestamp(),
        on_tokens_refreshed=_on_tokens_refreshed,
    )


# ---------------------------------------------------------------------------
# Google Docs / Drive HTTP calls
# ---------------------------------------------------------------------------


async def _fetch_document(access_token: str, document_id: str) -> dict[str, Any]:
    """GET the doc via ``documents.get`` with ``includeTabsContent=true``.

    Under this flag there is no top-level ``body`` -- content lives at
    ``tabs[].documentTab.body`` (see the module docstring and finding 6 in
    the design doc for the related bug in
    ``artemis/google_docs/client.py::import_google_document``, which omits
    this parameter). This function is a separate, minimal fetch -- it does
    not call or build on that existing importer.
    """
    async with httpx.AsyncClient(timeout=20.0) as http:
        resp = await http.get(
            f"{_DOCS_API_BASE}/documents/{document_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"includeTabsContent": "true"},
        )
    resp.raise_for_status()
    payload: dict[str, Any] = resp.json()
    return payload


async def _insert_text(
    access_token: str, *, document_id: str, tab_id: str, index: int, text: str
) -> None:
    """Exactly one ``insertText`` request. No other request kind, ever.

    This is THE insert-only guarantee: a single ``batchUpdate`` call with a
    ``requests`` list containing precisely one ``insertText`` entry. Never
    add a ``deleteContentRange`` or any content-replacing request to this
    function or to its caller.
    """
    body = {
        "requests": [
            {
                "insertText": {
                    "location": {"index": index, "tabId": tab_id},
                    "text": text,
                }
            }
        ]
    }
    async with httpx.AsyncClient(timeout=20.0) as http:
        resp = await http.post(
            f"{_DOCS_API_BASE}/documents/{document_id}:batchUpdate",
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
        )
    resp.raise_for_status()


async def _create_drive_comment(access_token: str, *, document_id: str, content: str) -> None:
    """``comments.create`` on the file -- requires the full ``drive`` scope.

    Mentions are triggered by the ``+email`` convention in ``content`` (the
    same one the Docs/Drive UI uses for an ``@mention``) -- no ``anchor`` is
    set; this is a general file-level comment, not tied to a specific
    revision region, so it carries no risk to document content at all.
    """
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(
            f"{_DRIVE_API_BASE}/files/{document_id}/comments",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": "id"},
            json={"content": content},
        )
    resp.raise_for_status()


async def write_doc_line(
    access_token: str,
    *,
    document_id: str,
    header: str,
    copy_hash: str,
    line_text: str,
) -> None:
    """Locate, insert, and verify. See the module docstring for the contract.

    Raises ``CardNotLocatedError`` if the target cannot be positively
    identified (nothing is written), or ``WritebackVerificationError`` if
    the insert succeeded but a re-read afterward shows a changed card count
    or a missing line (something IS already written at that point -- the
    caller must alert loudly and must not attempt a cleanup write).

    Public and generic on purpose (header + copy_hash + line_text -- nothing
    decision-specific): ``artemis.crisis_content.image_link`` (CCA10) calls
    this exact function for its own single doc-line write, rather than
    duplicating the locate/insert/verify sequence. See the module docstring.
    """
    before = await _fetch_document(access_token, document_id)
    location, before_count = locate_card_table(before, header=header, copy_hash=copy_hash)
    status_cell = _status_cell(location.table)
    if status_cell is None:
        raise CardNotLocatedError("Matched table has no status cell -- refusing to write")
    insert_index = _append_index_for_status_cell(status_cell)

    await _insert_text(
        access_token,
        document_id=document_id,
        tab_id=location.tab_id,
        index=insert_index,
        text="\n" + line_text,
    )

    after = await _fetch_document(access_token, document_id)
    after_count = len(_find_all_card_tables(after))
    try:
        after_location, _ = locate_card_table(after, header=header, copy_hash=copy_hash)
        line_present = line_text in _table_text(after_location.table)
    except CardNotLocatedError:
        line_present = False

    if after_count != before_count or not line_present:
        raise WritebackVerificationError(
            f"Post-write verification FAILED: before_count={before_count} "
            f"after_count={after_count} line_present={line_present} -- the document "
            "may be damaged. NOT attempting a cleanup write."
        )


# ---------------------------------------------------------------------------
# Owner alert -- separate copy of the same pattern poller.py uses
# ---------------------------------------------------------------------------


async def _alert_jon(session: AsyncSession, text: str) -> None:
    """Best-effort Slack DM to Jon via the Artemis bot. Never raises.

    Reuses the exact owner-alert path the poller's ``_alert_jon`` and the
    GCal/Gmail token-death handlers use
    (``artemis.proactivity.commitments._get_slack_token_for_agent`` +
    ``_resolve_artemis_dm_recipient``) rather than inventing a new one.
    """
    try:
        token = await _get_slack_token_for_agent(session, agent_id="artemis")
        if not token:
            logger.warning(
                "crisis_content writeback: no active Slack token for agent_id='artemis' "
                "-- cannot alert Jon"
            )
            return
        recipient_id = await _resolve_artemis_dm_recipient(session)
        await SlackClient(token=token).post_dm(user=recipient_id, text=text)
    except Exception:
        logger.exception("crisis_content writeback: failed to send owner alert DM")


# ---------------------------------------------------------------------------
# Idempotency ledger
# ---------------------------------------------------------------------------


async def _has_delivered(session: AsyncSession, decision_id: int, action: DeliveryAction) -> bool:
    stmt = (
        select(CrisisContentWritebackDelivery.id)
        .where(
            CrisisContentWritebackDelivery.decision_id == decision_id,
            CrisisContentWritebackDelivery.action == action,
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def _mark_delivered(session: AsyncSession, decision_id: int, action: DeliveryAction) -> None:
    """INSERT ... ON CONFLICT DO NOTHING, then commit this one action's row.

    Committing per-action (not once at the end of the three) is what makes
    the actions independently retryable: if the process dies or the email
    fails after the doc line already succeeded, the doc line's ledger row
    is already durable and a retry will not re-insert it.
    """
    stmt = (
        pg_insert(CrisisContentWritebackDelivery)
        .values(decision_id=decision_id, action=action)
        .on_conflict_do_nothing(constraint=_DELIVERY_CONSTRAINT)
    )
    await session.execute(stmt)
    await session.commit()


async def _load_card(session: AsyncSession, card_id: int) -> CrisisContentCard:
    result = await session.execute(select(CrisisContentCard).where(CrisisContentCard.id == card_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise CardNotLocatedError(f"No CrisisContentCard row for card_id={card_id}")
    return row


# ---------------------------------------------------------------------------
# The three actions, each independently idempotent
# ---------------------------------------------------------------------------


async def _deliver_doc_line(
    session: AsyncSession, decision: CrisisContentDecision
) -> DeliveryStatus:
    if await _has_delivered(session, decision.id, "doc_line"):
        return "already_delivered"

    card = await _load_card(session, decision.card_id)
    line_text = render_writeback_line(
        route=decision.route,
        decision=decision.decision,
        actor_label=_actor_label(decision),
        decided_at=decision.decided_at,
        note=decision.note,
    )

    try:
        access_token = await _resolve_docs_access_token(session)
        await write_doc_line(
            access_token,
            document_id=TARGET_DOCUMENT_ID,
            header=card.identity_header,
            copy_hash=card.copy_hash,
            line_text=line_text,
        )
    except CardNotLocatedError as exc:
        logger.error(
            "crisis_content writeback: target card not positively identified for "
            "decision_id=%s card_id=%s -- WRITING NOTHING. %s",
            decision.id,
            decision.card_id,
            exc,
        )
        await _alert_jon(
            session,
            "🚨 Crisis-content write-back: could not positively identify the target card "
            f"in Jen's doc for decision #{decision.id} ({card.identity_header!r}). Nothing "
            f"was written -- please check the doc by hand.\n{exc}",
        )
        return "not_located"
    except WritebackVerificationError as exc:
        logger.critical(
            "crisis_content writeback: POST-WRITE VERIFICATION FAILED for decision_id=%s "
            "-- the document may be damaged. %s",
            decision.id,
            exc,
        )
        await _alert_jon(
            session,
            "🚨🚨 Crisis-content write-back: wrote to Jen's doc but post-write "
            f"verification FAILED for decision #{decision.id} — {exc}\nDo NOT attempt a "
            "fix from here; check the doc by hand immediately.",
        )
        # The insertText call itself succeeded -- mark delivered so a retry
        # never attempts a second, compounding insert into a doc that may
        # already be damaged. See the module docstring.
        await _mark_delivered(session, decision.id, "doc_line")
        return "damaged"
    except _CredentialUnavailableError as exc:
        logger.error(
            "crisis_content writeback: doc line failed (credential) decision_id=%s: %s",
            decision.id,
            exc,
        )
        await _alert_jon(
            session, f"🚨 Crisis-content write-back could not run (Google credential): {exc}"
        )
        return "failed"
    except httpx.HTTPError as exc:
        logger.exception(
            "crisis_content writeback: doc line HTTP failure decision_id=%s", decision.id
        )
        await _alert_jon(
            session,
            f"🚨 Crisis-content write-back: doc write failed for decision #{decision.id}: {exc}",
        )
        return "failed"

    await _mark_delivered(session, decision.id, "doc_line")
    logger.info("crisis_content writeback: doc line delivered decision_id=%s", decision.id)
    return "delivered"


async def _deliver_comment(
    session: AsyncSession, decision: CrisisContentDecision
) -> DeliveryStatus:
    if await _has_delivered(session, decision.id, "comment"):
        return "already_delivered"

    card = await _load_card(session, decision.card_id)
    if card.is_test:
        # A card on the TESTING tab must never reach the external vendor.
        # CCA13 routes the NOTIFICATION to Jon's DM, but this runs later, off a
        # decision click, so the check has to happen here too -- reading the
        # is_test persisted on the card (migration 0111) rather than a
        # Transition that no longer exists by now.
        #
        # The doc LINE still writes (see deliver_decision_writeback): proving
        # the index math against the live document is the whole point of the
        # test lane, and it lands in a duplicated card where it is harmless.
        # What must not happen is Jen being @mentioned and emailed about a post
        # that does not exist.
        logger.info(
            "crisis_content writeback: skipping %s for TEST card_id=%s decision_id=%s",
            "Drive comment",
            card.id,
            decision.id,
        )
        return "skipped_test_card"

    content = _comment_content(card=card, decision=decision)

    try:
        access_token = await _resolve_docs_access_token(session)
        await _create_drive_comment(access_token, document_id=TARGET_DOCUMENT_ID, content=content)
    except Exception as exc:
        logger.exception(
            "crisis_content writeback: Drive comment failed decision_id=%s", decision.id
        )
        await _alert_jon(
            session,
            f"🚨 Crisis-content write-back: Drive comment failed for decision "
            f"#{decision.id}: {exc}",
        )
        return "failed"

    await _mark_delivered(session, decision.id, "comment")
    logger.info("crisis_content writeback: Drive comment delivered decision_id=%s", decision.id)
    return "delivered"


async def _deliver_email(session: AsyncSession, decision: CrisisContentDecision) -> DeliveryStatus:
    if await _has_delivered(session, decision.id, "email"):
        return "already_delivered"

    card = await _load_card(session, decision.card_id)
    if card.is_test:
        # A card on the TESTING tab must never reach the external vendor.
        # CCA13 routes the NOTIFICATION to Jon's DM, but this runs later, off a
        # decision click, so the check has to happen here too -- reading the
        # is_test persisted on the card (migration 0111) rather than a
        # Transition that no longer exists by now.
        #
        # The doc LINE still writes (see deliver_decision_writeback): proving
        # the index math against the live document is the whole point of the
        # test lane, and it lands in a duplicated card where it is harmless.
        # What must not happen is Jen being @mentioned and emailed about a post
        # that does not exist.
        logger.info(
            "crisis_content writeback: skipping %s for TEST card_id=%s decision_id=%s",
            "Gmail send",
            card.id,
            decision.id,
        )
        return "skipped_test_card"

    subject, body = _email_content(card=card, decision=decision)

    try:
        client = await _resolve_personal_gmail_client(session)
        await client.send_message(to=", ".join(jen_emails()), subject=subject, body=body)
    except Exception as exc:
        logger.exception("crisis_content writeback: Gmail send failed decision_id=%s", decision.id)
        await _alert_jon(
            session,
            f"🚨 Crisis-content write-back: email to Jen failed for decision #{decision.id}: {exc}",
        )
        return "failed"

    await _mark_delivered(session, decision.id, "email")
    logger.info("crisis_content writeback: email delivered decision_id=%s", decision.id)
    return "delivered"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def deliver_decision_writeback(
    session: AsyncSession, decision: CrisisContentDecision
) -> WritebackOutcome:
    """Run all three actions for one decision, each independently idempotent.

    Safe to call more than once for the SAME decision (retry, or a second
    delivery of the same Slack interactivity payload): each action checks
    ``crisis_content_writeback_deliveries`` before doing anything and skips
    if already delivered, so calling this twice never produces a second doc
    line, comment, or email. Never raises -- every failure inside one of the
    three actions is caught, logged, and alerted to Jon; a failure in one
    action never prevents the other two from running.
    """
    if not settings.crisis_content_writeback_enabled:
        logger.warning(
            "crisis_content writeback: disabled via settings "
            "(crisis_content_writeback_enabled=False) -- decision_id=%s will NOT be "
            "written back or notified until re-enabled",
            decision.id,
        )
        return WritebackOutcome(doc_line="disabled", comment="disabled", email="disabled")

    doc_line_status = await _deliver_doc_line(session, decision)
    comment_status = await _deliver_comment(session, decision)
    email_status = await _deliver_email(session, decision)
    return WritebackOutcome(doc_line=doc_line_status, comment=comment_status, email=email_status)


# ---------------------------------------------------------------------------
# Fire-and-forget scheduling -- called from slack_actions.py
# ---------------------------------------------------------------------------

# asyncio.create_task() returns a weakly-referenced Task; hold a strong ref
# here so it isn't GC'd before it runs. Mirrors the _BACKGROUND_TASKS pattern
# in artemis/routes/integrations_slack_events.py and
# artemis/floating_artemis/tools/argus_tools.py.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def schedule_decision_writeback(decision_id: int) -> None:
    """Fire-and-forget: run the write-back for one decision, off the request path.

    Called from ``artemis.crisis_content.slack_actions`` immediately after
    ``record_decision`` commits. Never awaited inline -- a Docs/Drive/Gmail
    round trip (with possible token refreshes) can take longer than Slack's
    3-second interactivity budget, and slack_actions.py's own contract is to
    ack Slack quickly and never raise. Opens its OWN session rather than
    reusing the request's -- the request's session may already be
    committed/closed by the time this task actually runs.
    """
    task = asyncio.create_task(_run_writeback_background(decision_id))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _run_writeback_background(decision_id: int) -> None:
    try:
        async with _db.SessionLocal() as session:
            result = await session.execute(
                select(CrisisContentDecision).where(CrisisContentDecision.id == decision_id)
            )
            decision = result.scalar_one_or_none()
            if decision is None:
                logger.error(
                    "crisis_content writeback: decision_id=%s vanished before the "
                    "background write-back could run",
                    decision_id,
                )
                return
            outcome = await deliver_decision_writeback(session, decision)
            logger.info("crisis_content writeback: decision_id=%s outcome=%r", decision_id, outcome)
    except Exception:
        logger.exception(
            "crisis_content writeback: unhandled error in background task for decision_id=%s",
            decision_id,
        )
