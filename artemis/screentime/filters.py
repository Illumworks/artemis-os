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
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from artemis.screentime.models import LEVEL_DISTRICT, LEVEL_STATE

_logger = logging.getLogger(__name__)

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
    action = [k.lower() for k in rules.get("restriction_action_keywords", [])]
    favorable = [k.lower() for k in rules.get("favorable_keywords", [])]
    # A pure cellphone-ban item with NO instructional-screen-time hook is out of lane.
    has_phone = any(k in lower for k in exclude)
    has_topic = (
        any(k in lower for k in restriction)
        or any(k in lower for k in action)
        or any(k in lower for k in favorable)
    )
    if has_phone and not has_topic:
        return False
    return has_topic


# --- Screen-time TOPIC-relevance gate ---------------------------------------
# This is the core data-quality fix. It runs BEFORE store/classify and keeps
# ONLY findings genuinely about instructional/student screen-time or device-time
# limits (and evidence-based-tool exemptions to such limits). It drops generic
# ed-policy (literacy, reading retention, curriculum approval, test scores).
#
# Distinct from is_screentime_relevant (which reuses the broad stance keywords:
# "limit"/"restrict"/"evidence-based") — those words are exactly what the
# reading-retention / literacy noise carries, which is why that check let the
# noise through. The topic gate requires an explicit SCREEN/DEVICE-time anchor.

# Decision the keyword pre-screen returns for an item.
TOPIC_KEEP = "keep"  # has a screen-time anchor, no exclude conflict → keep
TOPIC_DROP = "drop"  # no screen-time anchor (or only excluded themes) → drop
TOPIC_AMBIGUOUS = "ambiguous"  # has anchor AND an excluded theme → mixed signal


def _topic_terms(rules: dict[str, Any], key: str) -> list[str]:
    return [str(k).lower() for k in (rules.get(key) or []) if str(k).strip()]


def topic_prescreen(text: str, topic_rules: dict[str, Any]) -> str:
    """Pure keyword pre-screen → TOPIC_KEEP | TOPIC_DROP | TOPIC_AMBIGUOUS.

    KEEP       : a require-term (screen/device-time anchor) is present and no
                 excluded ed-policy theme is present.
    AMBIGUOUS  : a require-term AND an excluded theme are both present (e.g. a
                 reading-retention bill that also mentions "screen time"). The
                 caller decides: LLM tie-break if enabled, else KEEP (the anchor
                 wins — precision is enforced on the no-anchor path).
    DROP       : no require-term at all (the generic ed-policy noise), OR only an
                 excluded theme with no anchor.
    """
    lower = text.lower()
    require = _topic_terms(topic_rules, "require_any")
    exclude = _topic_terms(topic_rules, "exclude_any")

    has_anchor = any(term in lower for term in require)
    if not has_anchor:
        return TOPIC_DROP
    has_excluded = any(term in lower for term in exclude)
    if has_excluded:
        return TOPIC_AMBIGUOUS
    return TOPIC_KEEP


def passes_topic_gate(text: str, topic_rules: dict[str, Any]) -> bool:
    """Deterministic topic gate: True iff the item is screen-time-relevant.

    PURE, no I/O — the fast path used everywhere. Ambiguous (anchor + excluded
    theme) defaults to KEPT here; the async wrapper applies the optional LLM
    tie-break only when explicitly enabled.
    """
    return topic_prescreen(text, topic_rules) != TOPIC_DROP


async def passes_topic_gate_async(
    candidate: CandidateSignal,
    topic_rules: dict[str, Any],
    *,
    session: Any | None = None,
    llm_tiebreak: bool | None = None,
) -> bool:
    """Topic gate with an OPTIONAL cheap LLM tie-break for ambiguous items only.

    Cost discipline (per brief): the LLM is invoked ONLY for keyword-ambiguous
    items (require-term AND an excluded theme both present) AND only when the
    tie-break is enabled. Clear keeps/drops never hit a model. Failure-safe: any
    provider error falls back to KEEPING the ambiguous item (the anchor present
    means it is more likely on-topic than not).

    *llm_tiebreak* overrides the settings flag when given (tests). When None, the
    flag resolves from the per-config ``llm_tiebreak`` key OR the settings flag.
    """
    decision = topic_prescreen(candidate.text, topic_rules)
    if decision == TOPIC_DROP:
        return False
    if decision == TOPIC_KEEP:
        return True

    # AMBIGUOUS — decide whether to spend an LLM call.
    use_llm = llm_tiebreak
    if use_llm is None:
        use_llm = bool(topic_rules.get("llm_tiebreak"))
        try:
            from artemis.config import settings

            use_llm = use_llm or bool(settings.screentime_topic_llm_tiebreak)
        except Exception:  # pragma: no cover - settings guard
            pass
    if not use_llm:
        # Deterministic default: the anchor wins, keep it.
        return True

    verdict = await _llm_topic_relevant(candidate, session=session)
    # None = provider unreachable → failure-safe keep (anchor present).
    return True if verdict is None else verdict


async def _llm_topic_relevant(
    candidate: CandidateSignal,
    *,
    session: Any | None = None,
) -> bool | None:
    """Cheap tool-less LLM yes/no: is this item about instructional screen-time?

    Returns True/False, or None when no provider is reachable (caller treats
    None as failure-safe keep). Provider shape copied from classifier.classify_signal:
    ``complete_with_fallback(primary="codex", fallback="claude-code")`` with
    ``model=None`` INSIDE the CompletionRequest (NEVER a kwarg).
    """
    try:
        from artemis.agent.client import CompletionRequest
        from artemis.agent.types import Message, TextBlock
        from artemis.providers.fallback import complete_with_fallback
    except Exception:  # pragma: no cover - import guard
        _logger.warning("screentime.topic_gate: provider import failed; keeping ambiguous item", exc_info=True)
        return None

    system = (
        "You are a policy analyst for Amira Learning. Decide if a policy/legislation "
        "item is genuinely about INSTRUCTIONAL or STUDENT SCREEN-TIME / DEVICE-TIME "
        "limits in schools (including exemptions/carve-outs to such limits). It is NOT "
        "relevant if it is generic education policy whose mention of screens is "
        "incidental — e.g. reading retention, literacy mandates, curriculum/textbook "
        "approval, or test scores. Reply with ONLY compact JSON: "
        '{"relevant": true|false}. No prose.'
    )
    prompt = (
        f"Title: {candidate.title}\n"
        f"Summary: {candidate.summary}\n"
        "Is this genuinely about instructional/student screen-time or device-time limits?"
    )
    req = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=prompt)])],
        system=system,
        model=None,  # MUST be inside the request (CodexAdapter rejects a kwarg).
        max_tokens=50,
        cache_system=False,
        cache_tools=False,
    )
    try:
        resp = await complete_with_fallback(
            req,
            primary="codex",
            fallback="claude-code",
            session=session,
            feature_tag="screentime_topic_gate",
        )
    except Exception:
        _logger.warning("screentime.topic_gate: provider call failed; keeping ambiguous item", exc_info=True)
        return None

    answer = ""
    for block in resp.message.content:
        if hasattr(block, "text"):
            answer = block.text.strip()
            break
    return _parse_relevant(answer)


def _parse_relevant(text: str) -> bool | None:
    """Best-effort parse {"relevant": true|false} from a model reply."""
    import json

    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict) and isinstance(obj.get("relevant"), bool):
                return obj["relevant"]
        except (json.JSONDecodeError, ValueError):
            pass
    # Fallback: a bare yes/no in the text.
    low = text.lower()
    if "true" in low or low.startswith("yes"):
        return True
    if "false" in low or low.startswith("no"):
        return False
    return None


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
