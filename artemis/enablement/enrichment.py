"""AI-drafted catalog enrichment for the enablement library, with a review gate.

WHY
    All 416 assets have no summary and 129 have no audience, so nearly every Kai
    answer in #enablement-library carries "Caveat: Needs verification -- the
    catalog records don't include a summary." There is no format field (Sara
    asked for a Google Slides deck and got a PDF) and no grade range ("Reading
    Risk report: K-8 or PK-8?" was unanswerable). Kai performs well on thin
    data; this is the thin data.

THE SAFETY PROPERTY
    Owner decision (Jon, 2026-08-11): AI writes summaries directly, Sara and
    Missy review, and their feedback regenerates. That buys speed, and it would
    recreate the 2026-08-10 failure -- confident unverified claims presented as
    fact -- if Kai could not tell a draft from a reviewed record. So every
    generated summary lands as ``summary_status='ai_draft'`` and Kai caveats it
    until a human approves. Nothing here writes ``enablement_verified``; only
    the review path does.

GROUNDING
    The generator is given ONLY what the record already contains (title, name,
    type, tags, audience, sheet, transcript, searchable text). It is told to
    describe what the asset IS, never to assert efficacy, approval, or currency,
    and to leave a field null rather than guess. A summary that invents content
    is worse than no summary, because Kai will read it out as catalog fact once
    it is approved.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

_logger = logging.getLogger(__name__)

# Status vocabulary. Kept here so the routes, the tools, and the generator all
# agree on the spelling.
STATUS_AI_DRAFT = "ai_draft"
STATUS_VERIFIED = "enablement_verified"
STATUS_NEEDS_REVISION = "needs_revision"

VALID_STATUSES = frozenset({STATUS_AI_DRAFT, STATUS_VERIFIED, STATUS_NEEDS_REVISION})

# Controlled vocabulary for `format`. Free text here would defeat the point:
# Sara needs "give me the deck, not the PDF" to actually filter.
FORMATS = (
    "google_slides",
    "google_doc",
    "pdf",
    "video",
    "google_sheet",
    "web_page",
    "form",
    "demo_account",
    "other",
)

# Grade ranges as the field actually talks about them.
GRADE_RANGES = ("PK-2", "K-2", "PK-5", "K-5", "K-8", "PK-8", "3-5", "6-8", "9-12", "K-12")


class AssetEnrichment(BaseModel):
    """Validated shape of one enrichment draft.

    Every field except ``summary`` is optional on purpose: "I cannot tell from
    this record" must be representable, or the model will invent a value to fill
    the slot. That is the whole failure mode this catalog is trying to leave
    behind.
    """

    summary: str = Field(min_length=20, max_length=400)
    audience: str | None = None
    format: str | None = None
    grade_range: str | None = None

    @field_validator("summary")
    @classmethod
    def _summary_is_descriptive(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("summary must not be blank")
        return cleaned

    @field_validator("format")
    @classmethod
    def _known_format(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
        if normalized in ("", "null", "none", "unknown"):
            return None
        if normalized not in FORMATS:
            # Unknown vocabulary is dropped, not stored. A wrong format value is
            # worse than a missing one: it makes a filter silently lie.
            _logger.debug("enrichment: dropping unknown format %r", value)
            return None
        return normalized

    @field_validator("grade_range")
    @classmethod
    def _known_grade_range(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper().replace(" ", "")
        if normalized in ("", "NULL", "NONE", "UNKNOWN"):
            return None
        if normalized not in GRADE_RANGES:
            _logger.debug("enrichment: dropping unknown grade_range %r", value)
            return None
        return normalized

    @field_validator("audience")
    @classmethod
    def _clean_audience(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if cleaned.lower() in ("", "null", "none", "unknown"):
            return None
        return cleaned


@dataclass(frozen=True)
class AssetFacts:
    """Everything the generator is allowed to see. Nothing else exists to it."""

    drive_file_id: str
    title: str | None
    asset_name: str | None
    asset_type: str | None
    tags: list[str]
    audience: str | None
    source_sheet: str | None
    transcript_text: str | None
    searchable_text: str | None
    links: list[dict[str, Any]]
    #: Text actually fetched FROM the asset, when the backfill could open it.
    #: None means the file was never opened, and the prompt says so explicitly --
    #: 212 of the 289 unsummarised assets carry under 80 characters of record
    #: text, so summarising those from the record alone would paraphrase the
    #: title and pass it off as a description.
    document_text: str | None = None

    @classmethod
    def from_row(cls, asset: Any) -> AssetFacts:
        raw_links = asset.links or []
        return cls(
            drive_file_id=asset.drive_file_id,
            title=asset.title,
            asset_name=asset.asset_name,
            asset_type=asset.type,
            tags=list(asset.tags or []),
            audience=asset.audience,
            source_sheet=asset.source_sheet,
            transcript_text=asset.transcript_text,
            searchable_text=asset.searchable_text,
            links=[link for link in raw_links if isinstance(link, dict)],
        )


SYSTEM_PROMPT = """You write one-line catalog descriptions for a K-12 literacy \
company's enablement asset library. The descriptions are read by Kai, a librarian \
agent that hands assets to Customer Success and Sales, and by the Enablement team \
reviewing your work.

Describe what the asset IS and when someone would reach for it. Nothing else.

HARD RULES
- Use ONLY what you are given: the record fields, plus the DOCUMENT CONTENT \
excerpt when one is present. If no document content is provided you have NOT \
opened the file, and must not guess at what is inside it.
- Never assert that an asset is approved, current, up to date, the latest \
version, or effective. You have no way to know any of that, and a librarian \
repeating it as fact is the exact problem this catalog is fixing.
- Never invent content, section names, page counts, or claims about outcomes.
- If a field is not determinable from the record, return null for it. Do NOT \
guess to fill a slot. A null is useful; a wrong value silently breaks a filter.
- No emojis. No em dashes or en dashes. No marketing language.

summary: one or two plain sentences, 20 to 400 characters, describing what it is \
and who would use it. Start with the thing itself, not "This asset is".

audience: who it is for, e.g. Teacher, Admin, District Leader, Family, Student, \
CSM. Null if the record does not indicate it.

format: EXACTLY one of google_slides, google_doc, pdf, video, google_sheet, \
web_page, form, demo_account, other. Infer from the link URLs and the type field \
(docs.google.com/presentation is google_slides, a .pdf is pdf, a Drive video or a \
transcript present suggests video). Null if genuinely unclear.

grade_range: EXACTLY one of PK-2, K-2, PK-5, K-5, K-8, PK-8, 3-5, 6-8, 9-12, K-12. \
Null unless the record actually indicates a grade band. Most assets will be null, \
and that is correct.

Return ONLY a JSON object with keys: summary, audience, format, grade_range."""


def build_user_prompt(facts: AssetFacts, *, feedback: str | None = None) -> str:
    """Render the record into the prompt body.

    ``feedback`` is a reviewer's send-back note. When present it is the most
    important instruction in the prompt, because a human has already looked at
    the previous draft and said what was wrong with it.
    """
    lines: list[str] = ["Catalog record:"]
    lines.append(f"- title: {facts.title or '(none)'}")
    lines.append(f"- asset_name: {facts.asset_name or '(none)'}")
    lines.append(f"- type: {facts.asset_type or '(none)'}")
    lines.append(f"- tags: {', '.join(facts.tags) if facts.tags else '(none)'}")
    lines.append(f"- audience on record: {facts.audience or '(none)'}")
    lines.append(f"- source sheet: {facts.source_sheet or '(none)'}")

    if facts.links:
        lines.append("- links:")
        for link in facts.links[:6]:
            role = link.get("role", "?")
            visibility = link.get("visibility", "?")
            url = str(link.get("url", ""))[:160]
            lines.append(f"    [{role} / {visibility}] {url}")
    else:
        lines.append("- links: (none)")

    # Fetched document text outranks the record fields: it is the only source
    # here that describes what is actually IN the asset rather than how it was
    # filed. The record's own searchable_text is under 80 characters for 212 of
    # the 289 unsummarised assets -- little more than the title.
    body = facts.document_text or facts.transcript_text or facts.searchable_text
    if body:
        excerpt = " ".join(body.split())[: 4000 if facts.document_text else 1500]
        source = (
            "DOCUMENT CONTENT (fetched from the asset itself)"
            if facts.document_text
            else ("transcript" if facts.transcript_text else "document text")
        )
        lines.append(f"- {source} excerpt: {excerpt}")
    else:
        lines.append("- body text: (none available)")

    if feedback:
        lines.append("")
        lines.append(
            "A reviewer from the Enablement team rejected your previous draft "
            "with this note. Their note takes priority over your own reading of "
            "the record. Address it directly:"
        )
        lines.append(f'    "{feedback.strip()}"')

    lines.append("")
    lines.append("Return only the JSON object.")
    return "\n".join(lines)


def _parse_json(text: str) -> dict[str, Any] | None:
    """Best-effort extract a JSON object from a model reply."""
    text = text.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


async def generate_enrichment(
    facts: AssetFacts,
    *,
    feedback: str | None = None,
    session: Any | None = None,
) -> AssetEnrichment | None:
    """Draft one asset's enrichment. Returns None when it cannot be trusted.

    Returning None (rather than a filler summary) is deliberate: an asset with
    no summary is the status quo and is honest, while a hallucinated one becomes
    catalog fact the moment a reviewer clicks approve.
    """
    from artemis.agent.client import CompletionRequest
    from artemis.agent.types import Message, TextBlock
    from artemis.providers.fallback import complete_with_fallback

    request = CompletionRequest(
        messages=[
            Message(
                role="user",
                content=[TextBlock(text=build_user_prompt(facts, feedback=feedback))],
            )
        ],
        system=SYSTEM_PROMPT,
        max_tokens=500,
    )

    try:
        # claude-code primary: it is what Kai himself runs on, and it is the
        # provider verified working here on 2026-08-11. codex currently fails in
        # this environment with "Reading additional input from stdin", and that
        # error is not classified retryable, so complete_with_fallback re-raises
        # instead of falling through — a codex-primary call never reaches a
        # fallback at all. Tracked separately; it affects every codex-primary
        # caller, not just this one.
        response = await complete_with_fallback(
            request,
            primary="claude-code",
            fallback="codex",
            session=session,
            feature_tag="enablement_enrichment",
        )
    except Exception:
        _logger.warning(
            "enrichment: provider call failed for %s", facts.drive_file_id, exc_info=True
        )
        return None

    answer = ""
    for block in response.message.content:
        if hasattr(block, "text"):
            answer = block.text.strip()
            break

    parsed = _parse_json(answer)
    if parsed is None:
        _logger.warning("enrichment: unparseable reply for %s", facts.drive_file_id)
        return None

    try:
        return AssetEnrichment.model_validate(parsed)
    except Exception:
        _logger.warning(
            "enrichment: reply failed validation for %s: %r",
            facts.drive_file_id,
            parsed,
            exc_info=True,
        )
        return None


def embedding_text_for(asset: Any) -> str:
    """Rebuild the text an asset is embedded from.

    Mirrors ``_embedding_text`` in routes/enablement.py, which is the ingest-time
    source of truth: title + summary + tags + audience + searchable_text.
    """
    parts: list[str] = []
    if asset.title:
        parts.append(str(asset.title))
    if asset.summary:
        parts.append(str(asset.summary))
    for tag in asset.tags or []:
        if tag:
            parts.append(str(tag))
    if asset.audience:
        parts.append(str(asset.audience))
    if asset.searchable_text:
        parts.append(str(asset.searchable_text)[:4000])
    return " ".join(parts).strip()


async def reembed(asset: Any) -> bool:
    """Recompute the asset's vector after its summary changed.

    THIS IS THE POINT OF WRITING SUMMARIES. ``summary`` is part of the embedding
    input at ingest, but the embedding is only computed there -- so writing a
    summary straight to the row left the vector stale and the new text did
    nothing for semantic search. It reached keyword search (summary is in the
    LIKE clause) and nothing else. Found 2026-08-11 when Jon asked what the
    summaries were actually for.

    Returns True when the vector was updated. Never raises: a failed re-embed
    leaves the old vector in place, which is exactly the previous behaviour.
    """
    text = embedding_text_for(asset)
    if not text:
        return False
    try:
        from artemis.memory.embeddings import MiniLMProvider

        vector = await MiniLMProvider().embed(text)
    except Exception:
        _logger.warning(
            "enrichment: re-embed failed for %s; vector left stale",
            getattr(asset, "drive_file_id", "?"),
            exc_info=True,
        )
        return False
    if not vector:
        return False
    asset.embedding = vector
    return True


def apply_enrichment(asset: Any, enrichment: AssetEnrichment) -> None:
    """Write a draft onto the row. ALWAYS lands as ai_draft, never verified.

    Existing human-entered values win: audience is only filled where it is
    currently blank, so the 287 rows that already carry a curated audience are
    never overwritten by a guess.
    """
    asset.summary = enrichment.summary
    asset.summary_status = STATUS_AI_DRAFT
    asset.summary_generated_at = datetime.now(UTC)
    asset.summary_reviewed_by = None
    asset.summary_reviewed_at = None
    asset.summary_feedback = None

    if enrichment.audience and not (asset.audience or "").strip():
        asset.audience = enrichment.audience
    if enrichment.format and not (asset.format or "").strip():
        asset.format = enrichment.format
    if enrichment.grade_range and not (asset.grade_range or "").strip():
        asset.grade_range = enrichment.grade_range
