"""Brief-item reaction capture and engagement weighting for Artemis.

Mirrors the ticket-suppression pattern already in brief/sources.py (scope
agent:floating-artemis, FTS-keyed memory observations). No new DB table.

# Reaction capture
When Jon engages with a brief item (or mutes / ignores it), the reaction is
stored as a memory observation:

  Content format:
    brief_reaction:<item_type>:<item_key>:<reaction>

  Examples:
    brief_reaction:priority:MT-456-Fix-login-redirect:engage
    brief_reaction:priority:MT-456-Fix-login-redirect:ignore
    brief_reaction:waiting_on:Alice-PR-review:engage
    brief_reaction:okr:OKR-Q3-Sales-KR2:engage

  item_type: "priority" | "waiting_on" | "okr"
  reaction:  "engage" | "ignore" | "mute"

  The last reaction per (item_type, item_key) wins (same as brief_exclusion).

# Engagement weighting
read_engagement_weights() reads recent observations and returns a dict:

  { "<item_type>:<item_key>": float }   # float in [0.0, 2.0]

  engage → 1.5  (boost)
  ignore → 0.5  (down-rank)
  mute   → 0.0  (suppress entirely)
  missing → 1.0 (neutral)

The brief generator calls weight_brief_items() to sort/filter top_priorities
and waiting_on_you by the returned weights.

# Stability
- Observations are never deleted (lossless invariant).
- The last write per key wins because FTS returns newest-first.
- No LLM needed — item keys are normalised from deterministic content.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_REACTION_PREFIX = "brief_reaction:"
_VALID_REACTIONS = frozenset({"engage", "ignore", "mute"})
_VALID_ITEM_TYPES = frozenset({"priority", "waiting_on", "okr"})

# Boost/penalty multipliers applied to the default weight 1.0.
_REACTION_WEIGHTS: dict[str, float] = {
    "engage": 1.5,
    "ignore": 0.5,
    "mute": 0.0,
}

# ── Key normalisation ─────────────────────────────────────────────────────────


def _slugify(text: str) -> str:
    """Normalise text to a stable slug for use as an item key.

    Lowercases, strips punctuation, replaces runs of whitespace/hyphens with a
    single hyphen. Truncates at 80 chars to keep observation content short.
    """
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    return text[:80]


def make_item_key(item_type: str, label: str) -> str:
    """Return the composite key used in reaction observations.

    Args:
        item_type: "priority" | "waiting_on" | "okr"
        label: Human-readable label for the item (ticket title, person name, etc.)

    Returns:
        Normalised key string like "priority:mt-456-fix-login-redirect".
    """
    return f"{item_type}:{_slugify(label)}"


def make_reaction_content(item_type: str, label: str, reaction: str) -> str:
    """Build the memory observation content string for a reaction.

    Args:
        item_type: "priority" | "waiting_on" | "okr"
        label: Item label (ticket title, person name, etc.)
        reaction: "engage" | "ignore" | "mute"

    Returns:
        Content string like "brief_reaction:priority:mt-456-fix-login-redirect:engage"

    Raises:
        ValueError: If item_type or reaction is invalid.
    """
    if item_type not in _VALID_ITEM_TYPES:
        raise ValueError(
            f"Invalid item_type {item_type!r}; must be one of {sorted(_VALID_ITEM_TYPES)}"
        )
    if reaction not in _VALID_REACTIONS:
        raise ValueError(
            f"Invalid reaction {reaction!r}; must be one of {sorted(_VALID_REACTIONS)}"
        )
    return f"{_REACTION_PREFIX}{make_item_key(item_type, label)}:{reaction}"


# ── Observation parsing ───────────────────────────────────────────────────────


def parse_reaction_observations(observations: list[Any]) -> dict[str, float]:
    """Parse reaction observations into a weight map.

    Observations come back newest-first from FTS; we record only the first
    (newest) observation per composite key so the most-recent reaction wins.

    Args:
        observations: Memory observations from the agent:floating-artemis scope.
                      Accepts both Pydantic Observation objects (with .content)
                      and plain dicts.

    Returns:
        Dict mapping composite key (e.g. "priority:mt-456-fix-login-redirect")
        to a weight float in [0.0, 2.0]. Missing keys should be treated as 1.0.
    """
    seen: dict[str, float] = {}

    for obs in observations:
        content = (obs.content if hasattr(obs, "content") else obs.get("content", "")) or ""
        if not content.startswith(_REACTION_PREFIX):
            continue
        # Format: brief_reaction:<item_type>:<item_key>:<reaction>
        rest = content[len(_REACTION_PREFIX) :]
        # Split from the right to isolate the reaction token
        parts = rest.rsplit(":", 1)
        if len(parts) != 2:
            continue
        composite_key, reaction = parts[0], parts[1].strip()
        if reaction not in _VALID_REACTIONS:
            continue
        # Only record the first (newest) observation per composite key.
        if composite_key not in seen:
            seen[composite_key] = _REACTION_WEIGHTS[reaction]

    return seen


# ── Brief item weighting ──────────────────────────────────────────────────────


def weight_priorities(
    priorities: list[dict[str, Any]],
    weights: dict[str, float],
) -> list[dict[str, Any]]:
    """Sort and filter top_priorities by engagement weights.

    Items with weight=0.0 (muted) are dropped. Items are stable-sorted so
    higher-weighted items surface first. The list cap (max 3) is preserved.

    Args:
        priorities: List of BriefPriority dicts with at least an "item" key.
        weights: Weight map from parse_reaction_observations (or read_engagement_weights).

    Returns:
        Filtered and sorted list, max 3 items.
    """
    if not weights:
        return priorities[:3]

    scored: list[tuple[float, dict[str, Any]]] = []
    for p in priorities:
        label = (p.get("item") or "").strip()
        key = make_item_key("priority", label)
        w = weights.get(key, 1.0)
        if w == 0.0:
            continue  # muted — drop
        scored.append((w, p))

    # Stable sort: descending weight, then original order.
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:3]]


def weight_waiting_on(
    waiting: list[dict[str, Any]],
    weights: dict[str, float],
) -> list[dict[str, Any]]:
    """Sort and filter waiting_on_you by engagement weights.

    Same logic as weight_priorities. Muted items are dropped. Cap at 8.

    Args:
        waiting: List of WaitingItem dicts with at least a "who" key.
        weights: Weight map from parse_reaction_observations (or read_engagement_weights).

    Returns:
        Filtered and sorted list, max 8 items.
    """
    if not weights:
        return waiting[:8]

    scored: list[tuple[float, dict[str, Any]]] = []
    for w_item in waiting:
        label = (w_item.get("who") or "").strip()
        key = make_item_key("waiting_on", label)
        w = weights.get(key, 1.0)
        if w == 0.0:
            continue  # muted
        scored.append((w, w_item))

    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored[:8]]


# ── I/O helpers ───────────────────────────────────────────────────────────────


async def read_engagement_weights(session: Any) -> dict[str, float]:
    """Read reaction observations from memory and return a weight map.

    Convenience wrapper over fetch + parse for callers in the brief pipeline.

    Args:
        session: AsyncSession.

    Returns:
        Weight map (empty dict if memory unavailable — neutral/no effect).
    """
    try:
        from artemis.memory.retrieval import search_observations
        from artemis.memory.schemas import Scope

        scope = [Scope(scope_kind="agent", scope_id="floating-artemis")]
        results = await search_observations(
            session=session,
            scope_set=scope,
            query="brief_reaction",
            limit=100,
            modes=["fts"],
        )
        return parse_reaction_observations(list(results))
    except Exception:
        logger.debug("read_engagement_weights failed", exc_info=True)
        return {}


async def record_reaction(
    session: Any,
    *,
    item_type: str,
    label: str,
    reaction: str,
) -> None:
    """Write a brief-item reaction observation to memory.

    Idempotent: write_observation deduplicates by content hash, so re-sending
    the same reaction for the same item is a no-op. To change a reaction,
    write a new one — the most-recent wins on read.

    Args:
        session: AsyncSession (caller must commit).
        item_type: "priority" | "waiting_on" | "okr"
        label: Item label (ticket title, person name, etc.)
        reaction: "engage" | "ignore" | "mute"

    Raises:
        ValueError: If item_type or reaction is invalid.
    """
    from artemis.memory.schemas import Scope, SourceQualityHint
    from artemis.memory.store import write_observation

    content = make_reaction_content(item_type, label, reaction)
    scope = Scope(scope_kind="agent", scope_id="floating-artemis")
    await write_observation(
        session,
        scope=scope,
        content=content,
        category="convention",
        source_quality=SourceQualityHint.user,
        raw_source_kind="brief_reaction",
        raw_actor="artemis-proactivity",
    )
