"""Capture brief-item reactions from Jon's natural replies to the morning brief.

This closes the "learn from the morning brief" loop. The brief generator already
re-ranks itself from engagement weights (artemis/proactivity/brief_reactions.py +
artemis/brief/generator.py), and ``record_reaction`` already writes a reaction
observation. The missing piece — implemented here — is CAPTURE.

Design (settled; do not redesign):

- EXPLICIT-ONLY. We record a reaction only for brief items Jon explicitly
  references in a reply: "engage" (interest / wants to focus on / asks about an
  item) or "mute" (skip it / drop it / not now). We never infer "ignore" from
  silence — silence is not a data point.

- Labels must BIND to the brief's weighting keys. The weighting keys off the
  exact label strings (priorities[].item, waiting_on_you[].who). So on delivery
  we persist a "manifest" of the brief's exact item labels, and when classifying
  a reply we record reactions under those CANONICAL labels (not Jon's paraphrase)
  so ``brief_reactions.make_item_key`` matches.

Both I/O paths here are FAILURE-SAFE: they ride brief delivery and Jon's live
replies, so any error is logged and turns into a no-op (never raised).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# Manifest observations share the brief_reactions scope (agent:floating-artemis)
# and category so they live alongside the reaction observations.
_MANIFEST_PREFIX = "brief_manifest:"

# Re-export the valid item types from brief_reactions so the two stay in lockstep.
from artemis.proactivity.brief_reactions import _VALID_ITEM_TYPES  # noqa: E402


def _scope() -> Any:
    """Return the agent:floating-artemis scope (lazy import; mirrors brief_reactions)."""
    from artemis.memory.schemas import Scope

    return Scope(scope_kind="agent", scope_id="floating-artemis")


# ── Manifest persistence ────────────────────────────────────────────────────────


def _extract_manifest_items(brief: dict[str, Any]) -> list[dict[str, str]]:
    """Build the canonical-label manifest from a delivered brief dict.

    Field-name source of truth is artemis/proactivity/scheduler.py
    ``_format_brief_for_slack`` and artemis/brief/schemas.py:
      - brief["top_priorities"]: list[dict], each with key "item"   -> type "priority"
      - brief["waiting_on_you"]: list[dict], each with key "who"    -> type "waiting_on"
      - brief["okr_at_risk"]:    str | None (a single line, NOT a list) -> type "okr"

    Returns a list like [{"type": "priority", "label": "<item>"}, ...]; only the
    valid item types are emitted and empty labels are skipped.
    """
    items: list[dict[str, str]] = []

    for p in brief.get("top_priorities") or []:
        if not isinstance(p, dict):
            continue
        label = (p.get("item") or "").strip()
        if label:
            items.append({"type": "priority", "label": label})

    for w in brief.get("waiting_on_you") or []:
        if not isinstance(w, dict):
            continue
        label = (w.get("who") or "").strip()
        if label:
            items.append({"type": "waiting_on", "label": label})

    # okr_at_risk is a single string (or None), so the line itself is the label.
    okr_line = (brief.get("okr_at_risk") or "").strip() if isinstance(brief.get("okr_at_risk"), str) else ""
    if okr_line:
        items.append({"type": "okr", "label": okr_line})

    # Belt-and-suspenders: only keep valid item types.
    return [it for it in items if it["type"] in _VALID_ITEM_TYPES]


async def persist_brief_manifest(session: Any, brief: dict[str, Any]) -> None:
    """Persist the delivered brief's canonical item labels as one observation.

    Content format: ``brief_manifest:<iso-timestamp>:<json-list-of-items>``.
    Written via memory.store.write_observation in the agent:floating-artemis
    scope, category "convention" — the same call shape ``record_reaction`` uses.

    FAILURE-SAFE: any error is logged and swallowed; never raises into the brief
    delivery path. The caller still owns the commit.
    """
    try:
        items = _extract_manifest_items(brief)
        if not items:
            logger.debug("persist_brief_manifest: no items in brief — skipping manifest write")
            return

        from artemis.memory.schemas import SourceQualityHint
        from artemis.memory.store import write_observation

        content = f"{_MANIFEST_PREFIX}{datetime.now(UTC).isoformat()}:" + json.dumps(items)
        await write_observation(
            session,
            scope=_scope(),
            content=content,
            category="convention",
            source_quality=SourceQualityHint.user,
            raw_source_kind="brief_manifest",
            raw_actor="artemis-proactivity",
        )
        logger.debug("persist_brief_manifest: wrote manifest with %d item(s)", len(items))
    except Exception:
        logger.warning("persist_brief_manifest failed — manifest not persisted", exc_info=True)
        return


async def _load_recent_manifest(session: Any, max_age_hours: int = 36) -> list[dict] | None:
    """Load the newest brief manifest within ``max_age_hours``.

    Mirrors ``brief_reactions.read_engagement_weights``: FTS search the
    agent:floating-artemis scope for "brief_manifest", parse each observation,
    pick the newest by its embedded ISO timestamp, and return its item list — or
    None if there is no manifest or the newest one is too old.

    Content layout is ``brief_manifest:<iso>:<json>``. The ISO timestamp itself
    contains colons, so we locate the JSON by the first ``[`` after the prefix
    and treat everything between the prefix and that ``[`` (minus the trailing
    ``:``) as the timestamp.

    FAILURE-SAFE: any error returns None.
    """
    try:
        from artemis.memory.retrieval import search_observations

        results = await search_observations(
            session=session,
            scope_set=[_scope()],
            query="brief_manifest",
            limit=5,
            modes=["fts"],
        )

        best_ts: datetime | None = None
        best_items: list[dict] | None = None

        for obs in results:
            content = obs.content or ""
            if not content.startswith(_MANIFEST_PREFIX):
                continue
            rest = content[len(_MANIFEST_PREFIX):]
            bracket = rest.find("[")
            if bracket <= 0:
                continue
            iso_part = rest[: bracket - 1] if rest[bracket - 1] == ":" else rest[:bracket]
            iso_part = iso_part.rstrip(":")
            json_part = rest[bracket:]
            try:
                ts = datetime.fromisoformat(iso_part)
                items = json.loads(json_part)
            except Exception:
                continue
            if not isinstance(items, list):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if best_ts is None or ts > best_ts:
                best_ts = ts
                best_items = items

        if best_ts is None or best_items is None:
            return None

        age = datetime.now(UTC) - best_ts
        if age.total_seconds() > max_age_hours * 3600:
            logger.debug(
                "_load_recent_manifest: newest manifest is %.1fh old (> %dh) — ignoring",
                age.total_seconds() / 3600,
                max_age_hours,
            )
            return None

        return best_items
    except Exception:
        logger.warning("_load_recent_manifest failed — returning None", exc_info=True)
        return None


# ── Reaction capture from a reply ────────────────────────────────────────────────


def _build_classify_prompt(items: list[dict], message_text: str) -> str:
    """Build the strict-JSON classification prompt for the reply."""
    lines: list[str] = [
        "You classify how Jon reacted to specific items from his morning brief.",
        "",
        "Here are the brief items (numbered, with their exact labels):",
    ]
    for i, it in enumerate(items, start=1):
        label = str(it.get("label", "")).strip()
        lines.append(f'{i}. [{it.get("type", "")}] {label}')
    lines.extend(
        [
            "",
            "Jon's reply:",
            f'"""{message_text}"""',
            "",
            "Return STRICT JSON: a list of objects, one per brief item Jon "
            "EXPLICITLY references. Each object is "
            '{"index": <the item number from the list above>, "reaction": "engage"|"mute"}.',
            '- "engage": he shows interest, wants to focus on, or asks about the item.',
            '- "mute": he says skip it, drop it, not now, or otherwise dismisses it.',
            "Only include items he explicitly references; omit everything else. "
            "Never infer a reaction from silence. If he references none, return [].",
            "Output JSON only, no prose.",
        ]
    )
    return "\n".join(lines)


def _parse_classify_json(raw: str) -> list[dict]:
    """Parse the model's JSON reply robustly (strip code fences, tolerate junk).

    Returns a list of dicts, or [] on any parse failure.
    """
    if not raw:
        return []
    text = raw.strip()
    # Strip ``` / ```json code fences.
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[: -3]
        text = text.strip()
    # Locate the first JSON array.
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [p for p in parsed if isinstance(p, dict)]


async def capture_brief_reactions_from_message(
    session: Any, message_text: str
) -> list[tuple[str, str, str]]:
    """Record explicit brief reactions from a single inbound message.

    1. Load the recent manifest. If none/empty → return [] WITHOUT calling the LLM.
    2. LLM-classify which manifest items Jon explicitly references and how
       ("engage" / "mute"), under their canonical labels.
    3. For each valid {label, reaction}: map the paraphrase-free canonical label
       back to its (type, label), call ``record_reaction``, commit.

    Returns the list of (item_type, canonical_label, reaction) actually recorded.

    FAILURE-SAFE: any exception is logged and yields []. Runs fire-and-forget off
    Jon's reply, so it must never raise.
    """
    try:
        if not message_text or not message_text.strip():
            return []

        items = await _load_recent_manifest(session)
        if not items:
            return []

        # The model references items by their 1-based index in this list, so the
        # canonical (type, label) binding is exact — no fragile label echoing.
        if not any(
            str(it.get("type", "")).strip() in _VALID_ITEM_TYPES
            and str(it.get("label", "")).strip()
            for it in items
        ):
            return []

        # ── LLM classify (lazy provider import — circular-import rule) ─────────
        from artemis.agent.client import CompletionRequest
        from artemis.agent.types import Message, TextBlock
        from artemis.providers.fallback import complete_with_fallback
        from artemis.proactivity.brief_reactions import record_reaction

        prompt = _build_classify_prompt(items, message_text)
        req = CompletionRequest(
            messages=[Message(role="user", content=[TextBlock(text=prompt)])],
            model=None,  # tool-less classify — let the adapter use its default
            max_tokens=400,
            cache_system=False,
            cache_tools=False,
        )
        # Tool-less classify → codex primary is fine + cheap; claude-code fallback.
        resp = await complete_with_fallback(
            req,
            primary="codex",
            fallback="claude-code",
            feature_tag="brief_reaction_capture",
        )
        raw = ""
        for block in resp.message.content:
            if hasattr(block, "text"):
                raw += block.text

        decisions = _parse_classify_json(raw)
        if not decisions:
            return []

        recorded: list[tuple[str, str, str]] = []
        seen_idx: set[int] = set()
        for d in decisions:
            reaction = str(d.get("reaction", "")).strip().lower()
            if reaction not in {"engage", "mute"}:
                continue
            try:
                idx = int(d.get("index"))
            except (TypeError, ValueError):
                continue
            # 1-based index into the manifest; ignore out-of-range / duplicates.
            if idx < 1 or idx > len(items) or idx in seen_idx:
                continue
            seen_idx.add(idx)
            it = items[idx - 1]
            item_type = str(it.get("type", "")).strip()
            canonical_label = str(it.get("label", "")).strip()
            if item_type not in _VALID_ITEM_TYPES or not canonical_label:
                continue
            await record_reaction(
                session,
                item_type=item_type,
                label=canonical_label,
                reaction=reaction,
            )
            recorded.append((item_type, canonical_label, reaction))

        if recorded:
            await session.commit()
            logger.info(
                "capture_brief_reactions_from_message: recorded %d reaction(s): %s",
                len(recorded),
                recorded,
            )
        return recorded
    except Exception:
        logger.warning(
            "capture_brief_reactions_from_message failed — no reactions recorded", exc_info=True
        )
        return []
