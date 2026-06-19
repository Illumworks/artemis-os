"""Normalisation, dedupe, and the "real moves" filter for Screen-Time Watch.

The scouts each return slightly different finding-dict shapes (legislative uses
``metadata.bill_*``; state_doe/regional_news use ``metadata.source_url``;
board_minutes uses district context). ``normalize_finding`` flattens any of them
into one canonical :class:`CandidateSignal`, screen-time-relevant or not.

The **"real moves" filter** then keeps only *actual* legislative/board actions
(bill introduced / passed / amended, policy adopted, dept guidance) that are
*about instructional screen-time* — and drops generic headlines, opinion, and
out-of-lane cellphone-ban chatter. The bar is explicit + unit-tested.

All functions here are PURE (no I/O, no async, no provider calls).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from artemis.screentime.models import LEVEL_DISTRICT, LEVEL_STATE

# Maps each scout's emitted sourceType / discoveredBy to our canonical
# source_type vocabulary (legislative | state_doe | board_minutes | regional_news).
_SOURCE_TYPE_MAP: dict[str, str] = {
    "legiscan": "legislative",
    "legislative_scout": "legislative",
    "state_doe": "state_doe",
    "state_doe_scout": "state_doe",
    "board_minutes": "board_minutes",
    "board_minutes_scout": "board_minutes",
    "boarddocs": "board_minutes",
    "granicus": "board_minutes",
    "regional_news": "regional_news",
    "regional_news_scout": "regional_news",
    "newsapi": "regional_news",
}

# --- "real move" status detection -------------------------------------------
# A "real move" is an actual action. We classify the status from the text /
# metadata; only these statuses survive the filter.
STATUS_PROPOSED = "proposed"
STATUS_PASSED = "passed"
STATUS_AMENDED = "amended"
STATUS_GUIDANCE = "guidance"
STATUS_NEWS = "news"  # NOT a real move on its own

_REAL_MOVE_STATUSES: frozenset[str] = frozenset(
    {STATUS_PROPOSED, STATUS_PASSED, STATUS_AMENDED, STATUS_GUIDANCE}
)

# Phrases that mark an actual legislative/board action (vs. a headline/opinion).
_ACTION_PATTERNS: list[tuple[str, str]] = [
    (r"\bpassed\b|\benacted\b|\bsigned into law\b|\benrolled\b|\bapproved\b|\badopted\b", STATUS_PASSED),
    (r"\bamend(ed|ment)\b|\bsubstitut(e|ed)\b|\brevis(ed|ion)\b", STATUS_AMENDED),
    (r"\bintroduc(ed|tion)\b|\bfiled\b|\bproposed\b|\bbill\b|\bhb ?\d|\bsb ?\d|\bprefiled\b", STATUS_PROPOSED),
    (r"\bguidance\b|\bmemo\b|\bdirective\b|\bpolicy\b|\brule\b|\bregulation\b|\bstandards?\b", STATUS_GUIDANCE),
]

# Opinion / headline markers — even if other words match, these alone never qualify.
_OPINION_MARKERS = re.compile(
    r"\b(opinion|op-ed|editorial|column|commentary|analysis|explainer|what to know|here'?s why)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class CandidateSignal:
    """Canonical, source-agnostic shape produced from any scout finding."""

    state: str
    title: str
    summary: str
    source_type: str
    source_url: str
    level: str = LEVEL_STATE
    district_name: str | None = None
    status: str = STATUS_NEWS
    published_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Combined searchable text for keyword/classification work."""
        return f"{self.title}\n{self.summary}".strip()

    @property
    def content_hash(self) -> str:
        return compute_content_hash(self.source_type, self.source_url, self.title)


def compute_content_hash(source_type: str, source_url: str, title: str) -> str:
    """Stable dedup key. Same source+url+title → same hash (idempotent re-runs)."""
    basis = f"{source_type}|{(source_url or '').strip().lower()}|{(title or '').strip().lower()}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _coerce_state(value: Any) -> str:
    s = str(value or "").strip().upper()
    # legislative emits districtId="STATE_FL"; strip the prefix.
    if s.startswith("STATE_"):
        s = s[len("STATE_") :]
    return s[:2] if len(s) >= 2 and s.isalpha() else s


def normalize_finding(finding: dict[str, Any]) -> CandidateSignal | None:
    """Flatten ANY scout finding dict into a CandidateSignal.

    Returns None when the finding has no usable state — we never store a
    stateless screen-time signal (the heat map is per-state).
    """
    meta: dict[str, Any] = finding.get("metadata") or {}

    raw_source = str(finding.get("sourceType") or finding.get("discoveredBy") or "").lower()
    source_type = _SOURCE_TYPE_MAP.get(raw_source, "regional_news")

    state = _coerce_state(meta.get("state") or finding.get("state") or finding.get("districtId"))
    if not state:
        return None

    title = str(
        finding.get("title")
        or meta.get("title")
        or meta.get("headline")
        or finding.get("evidence")
        or ""
    ).strip()
    summary = str(
        finding.get("summary")
        or finding.get("evidence")
        or meta.get("summary")
        or meta.get("description")
        or ""
    ).strip()
    if not title:
        title = (summary[:120] + "…") if len(summary) > 120 else summary
    if not title:
        return None

    source_url = str(
        finding.get("source_url")
        or meta.get("source_url")
        or meta.get("url")
        or meta.get("link")
        or ""
    ).strip()

    district_name = (
        finding.get("district_name")
        or meta.get("district_name")
        or meta.get("source_name")
        or None
    )
    level = LEVEL_DISTRICT if (source_type == "board_minutes" or district_name) else LEVEL_STATE

    published_at = _parse_dt(meta.get("published_at") or meta.get("date") or finding.get("published_at"))

    status = _classify_status(f"{title} {summary}", source_type, meta)

    return CandidateSignal(
        state=state,
        title=title,
        summary=summary,
        source_type=source_type,
        source_url=source_url,
        level=level,
        district_name=str(district_name) if district_name else None,
        status=status,
        published_at=published_at,
        raw={"finding": finding},
    )


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _classify_status(text: str, source_type: str, meta: dict[str, Any]) -> str:
    """Infer the action status from text + metadata. Defaults to 'news'."""
    # Legislative scout carries an explicit numeric status_code — trust it first.
    status_code = meta.get("status_code")
    if isinstance(status_code, int):
        if status_code >= 4:
            return STATUS_PASSED
        if status_code in (2, 3):
            return STATUS_AMENDED
        if status_code == 1:
            return STATUS_PROPOSED
    if source_type == "legislative":
        # A legislative finding without explicit code is still a bill action.
        for pattern, status in _ACTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return status
        return STATUS_PROPOSED

    for pattern, status in _ACTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return status
    return STATUS_NEWS


def is_screentime_relevant(text: str, rules: dict[str, Any]) -> bool:
    """True iff the text concerns instructional screen-time (and isn't a phone ban)."""
    lower = text.lower()
    exclude = [k.lower() for k in rules.get("exclude_keywords", [])]
    restriction = [k.lower() for k in rules.get("restriction_keywords", [])]
    favorable = [k.lower() for k in rules.get("favorable_keywords", [])]
    # A pure cellphone-ban item with NO instructional-screen-time hook is out of lane.
    has_phone = any(k in lower for k in exclude)
    has_topic = any(k in lower for k in restriction) or any(k in lower for k in favorable)
    if has_phone and not has_topic:
        return False
    return has_topic


def is_real_move(candidate: CandidateSignal, rules: dict[str, Any]) -> bool:
    """The explicit, testable "real moves" bar.

    Keep a candidate iff ALL hold:
      1. status is an actual action (proposed | passed | amended | guidance) —
         a bare 'news' item is dropped.
      2. it's screen-time relevant (and not an out-of-lane cellphone ban).
      3. it is not flagged opinion/op-ed/editorial in the title.
    """
    if candidate.status not in _REAL_MOVE_STATUSES:
        return False
    if not is_screentime_relevant(candidate.text, rules):
        return False
    return not _OPINION_MARKERS.search(candidate.title)


def dedupe(candidates: list[CandidateSignal]) -> list[CandidateSignal]:
    """Drop within-batch duplicates by content_hash (first occurrence wins)."""
    seen: set[str] = set()
    out: list[CandidateSignal] = []
    for c in candidates:
        h = c.content_hash
        if h in seen:
            continue
        seen.add(h)
        out.append(c)
    return out
