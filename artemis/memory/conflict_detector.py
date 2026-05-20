"""Memory M2 — Pure conflict detection logic.

Given a new observation and a list of existing observations from the same
(scope_id, entity_key), returns ConflictCandidate objects for any detected
contradictions.

Three detector functions:
  _detect_incompatible_values     — same attribute, different value, overlapping windows
  _detect_incompatible_temporal   — one claim's end precedes another's start
  _detect_incompatible_relational — A says X relates to Y, B says X NOT-related to Y

All detectors are pure — no DB access. Callers pre-fetch the comparison set.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from artemis.memory.schemas import Observation

# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class ConflictCandidate:
    """A potential conflict between new_obs and an existing observation."""

    existing_id: int
    conflict_type: str  # incompatible_values | incompatible_temporal | incompatible_relational


# ── Validity window helpers ───────────────────────────────────────────────────


def _windows_overlap(
    a_from: datetime | None,
    a_until: datetime | None,
    b_from: datetime | None,
    b_until: datetime | None,
) -> bool:
    """Return True when the two validity intervals overlap.

    None valid_from → open left (from beginning of time).
    None valid_until → open right (currently valid).
    """
    # A ends before B starts, OR B ends before A starts → no overlap
    return not (
        (a_until is not None and b_from is not None and a_until <= b_from)
        or (b_until is not None and a_from is not None and b_until <= a_from)
    )


# ── Detector 1: incompatible values ──────────────────────────────────────────


def _detect_incompatible_values(
    new_obs: Observation,
    existing_observations: list[Observation],
) -> list[ConflictCandidate]:
    """Detect same (scope_id, entity_key, attribute_key) with a different value
    and overlapping validity windows.

    Heuristic: observations are considered to address the same attribute when
    the first `attribute_key_prefix` tokens of their content are identical and
    they differ in the remainder. This is intentionally simple — M2 relies on
    writers setting meaningful, structured content; M6 will add LLM-assisted
    attribute resolution.

    The check used here: if two observations share the same scope and the first
    8 words of their content match (the "attribute key prefix") but the full
    content differs — they are incompatible values for the same attribute.
    """
    conflicts: list[ConflictCandidate] = []
    new_tokens = new_obs.content.split()
    prefix_len = min(8, len(new_tokens))
    new_prefix = " ".join(new_tokens[:prefix_len])

    for existing in existing_observations:
        if existing.id == new_obs.id:
            continue
        if existing.superseded_by is not None:
            continue  # skip already-retired observations
        if existing.scope_kind != new_obs.scope_kind or existing.scope_id != new_obs.scope_id:
            continue

        ex_tokens = existing.content.split()
        ex_prefix_len = min(8, len(ex_tokens))
        ex_prefix = " ".join(ex_tokens[:ex_prefix_len])

        if (
            new_prefix == ex_prefix
            and new_obs.content != existing.content
            and _windows_overlap(
                new_obs.valid_from,
                new_obs.valid_until,
                existing.valid_from,
                existing.valid_until,
            )
        ):
            conflicts.append(
                ConflictCandidate(
                    existing_id=existing.id,
                    conflict_type="incompatible_values",
                )
            )
    return conflicts


# ── Detector 2: incompatible temporal ────────────────────────────────────────


def _detect_incompatible_temporal(
    new_obs: Observation,
    existing_observations: list[Observation],
) -> list[ConflictCandidate]:
    """Detect temporal incoherence: one observation's valid_from is after
    another's valid_until but both claim to be about the same entity.

    Specifically: if existing.valid_until is set and new_obs.valid_from is set
    and new_obs.valid_from < existing.valid_until — the new claim predates the
    end of the old claim. OR if new_obs.valid_until is set and it is before
    existing.valid_from (new claim ends before old claim starts) — these cannot
    both be right for a monotonically-advancing timeline.

    The check requires both observations to be in the same scope.
    """
    conflicts: list[ConflictCandidate] = []

    for existing in existing_observations:
        if existing.id == new_obs.id:
            continue
        if existing.superseded_by is not None:
            continue
        if existing.scope_kind != new_obs.scope_kind or existing.scope_id != new_obs.scope_id:
            continue

        # Case: existing says it ended (valid_until set), new says it starts
        # BEFORE that end — meaning both claim overlapping truths for the
        # same entity across the same period. Flag as temporal incoherence
        # only when both have explicit temporal anchors.
        if (
            existing.valid_until is not None
            and new_obs.valid_from is not None
            and new_obs.valid_until is not None
            and (new_obs.valid_until <= existing.valid_from if existing.valid_from else False)
        ):
            # New claim is entirely before existing claim ended — temporal gap
            conflicts.append(
                ConflictCandidate(
                    existing_id=existing.id,
                    conflict_type="incompatible_temporal",
                )
            )
            continue

        # Case: new observation ends before existing observation starts
        if (
            new_obs.valid_until is not None
            and existing.valid_from is not None
            and new_obs.valid_until <= existing.valid_from
            and _windows_overlap(
                new_obs.valid_from,
                new_obs.valid_until,
                existing.valid_from,
                existing.valid_until,
            )
            is False
        ):
            # This is a gap, not an overlap — flag temporal incoherence when
            # both observations share enough content similarity (first 4 tokens).
            new_prefix = " ".join(new_obs.content.split()[:4])
            ex_prefix = " ".join(existing.content.split()[:4])
            if new_prefix == ex_prefix:
                conflicts.append(
                    ConflictCandidate(
                        existing_id=existing.id,
                        conflict_type="incompatible_temporal",
                    )
                )

    return conflicts


# ── Detector 3: incompatible relational ──────────────────────────────────────


def _detect_incompatible_relational(
    new_obs: Observation,
    existing_observations: list[Observation],
) -> list[ConflictCandidate]:
    """Detect relational contradictions: one observation asserts a relation,
    another asserts its negation, for overlapping validity windows.

    Heuristic: content starting with "NOT_" or containing " NOT " immediately
    before a relation term signals negation. A positive claim and its negation
    for the same subject-object pair in overlapping windows constitute a conflict.

    The actual negation prefix used by callers is "NOT_RELATES_TO" vs
    "RELATES_TO" in the content. We detect the pattern: if one obs contains
    "RELATES_TO" in its content and another contains "NOT_RELATES_TO" (or
    "DOES_NOT_RELATE_TO") for the same pair — conflict.
    """
    conflicts: list[ConflictCandidate] = []
    new_content_upper = new_obs.content.upper()

    # Detect whether this observation is a positive or negative relational claim
    is_new_positive = "RELATES_TO" in new_content_upper and "NOT_RELATES_TO" not in new_content_upper
    is_new_negative = "NOT_RELATES_TO" in new_content_upper or "DOES_NOT_RELATE_TO" in new_content_upper

    if not (is_new_positive or is_new_negative):
        return conflicts

    for existing in existing_observations:
        if existing.id == new_obs.id:
            continue
        if existing.superseded_by is not None:
            continue
        if existing.scope_kind != new_obs.scope_kind or existing.scope_id != new_obs.scope_id:
            continue

        ex_content_upper = existing.content.upper()
        is_ex_positive = (
            "RELATES_TO" in ex_content_upper
            and "NOT_RELATES_TO" not in ex_content_upper
        )
        is_ex_negative = (
            "NOT_RELATES_TO" in ex_content_upper
            or "DOES_NOT_RELATE_TO" in ex_content_upper
        )

        # Conflict: one positive, one negative, with overlapping windows
        is_contradiction = (is_new_positive and is_ex_negative) or (
            is_new_negative and is_ex_positive
        )
        if is_contradiction and _windows_overlap(
            new_obs.valid_from,
            new_obs.valid_until,
            existing.valid_from,
            existing.valid_until,
        ):
            conflicts.append(
                ConflictCandidate(
                    existing_id=existing.id,
                    conflict_type="incompatible_relational",
                )
            )

    return conflicts


# ── Public entry point ────────────────────────────────────────────────────────


def detect_conflicts(
    new_observation: Observation,
    existing_observations: list[Observation],
) -> list[ConflictCandidate]:
    """Run all three detectors; return deduplicated conflict candidates.

    existing_observations should be pre-fetched by the caller (same scope_id,
    active validity window). The new_observation may or may not yet have an id
    (unsaved) — detectors skip by id equality only when id is set.
    """
    seen: set[int] = set()
    results: list[ConflictCandidate] = []

    for candidate in (
        _detect_incompatible_values(new_observation, existing_observations)
        + _detect_incompatible_temporal(new_observation, existing_observations)
        + _detect_incompatible_relational(new_observation, existing_observations)
    ):
        if candidate.existing_id not in seen:
            seen.add(candidate.existing_id)
            results.append(candidate)

    return results
