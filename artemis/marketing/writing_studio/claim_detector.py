"""Claim-flag detection — conservative, deterministic, no LLM.

Stage 4 of the Composer rebuild.  This module:

  1. Scans draft text for "strong-claim" candidate spans (quantified, superlative/
     exclusivity, comparative/category patterns).
  2. For each candidate, computes a deterministic token-set similarity score
     against the profile's APPROVED claims from the Claims Register.
  3. SUPPRESSES candidates that are sufficiently similar to an approved claim
     (score >= SUPPRESS_THRESHOLD) — they are already registered language.
  4. Returns the remaining candidates as flags with the top 1-2 nearest approved
     claims for popover context.

Design goals:
- Bias toward NOT flagging.  False-negatives (missed claims) are cheaper than
  nagging on ordinary copy (Jon).
- Pattern classes and thresholds are module-level constants so Lead can tune.
- No external dependencies beyond the Python stdlib (re, string).
- Complexity is O(candidates × approved_claims); fast at realistic sizes (~100
  approved claims, <10 candidate spans per draft).
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field

# ─────────────────────────────────────────────────────────────────────────────
# Tunable constants — Lead can adjust without touching logic.
# ─────────────────────────────────────────────────────────────────────────────

# Token-set similarity threshold for SUPPRESSION.  A candidate whose best
# match against the approved register is >= this value is considered "already
# approved" and is NOT returned as a flag.
# Range 0.0–1.0.  Lower = more suppression; higher = more flags.
SUPPRESS_THRESHOLD: float = 0.60

# Maximum number of nearest-approved entries returned in each flag's context.
MAX_NEAREST: int = 2

# Pattern classes that are ACTIVE.  Set a value to False to disable that class
# without deleting the regex.
PATTERN_CLASSES: dict[str, bool] = {
    "quantified": True,  # percentages, Nx, score points, counts, durations
    "superlative": True,  # only, first, best, #1, most, leading, proven, …
    "comparative": True,  # more than, outperforms, vs., compared to
}

# Minimum character length for a candidate span.  Very short matches are almost
# always noise.
MIN_CANDIDATE_CHARS: int = 15

# Minimum word count for a candidate span.  Guards against lone numbers.
MIN_CANDIDATE_WORDS: int = 3

# ─────────────────────────────────────────────────────────────────────────────
# Pattern definitions (compiled once at import time)
# ─────────────────────────────────────────────────────────────────────────────
#
# Each pattern is designed to match the CLAUSE or SENTENCE containing the
# strong-claim signal — not just the signal token itself — so the returned span
# has enough context for the popover.  We use sentence-level anchoring with
# lookahead/lookbehind at sentence boundaries.
#
# Sentence-fragment extraction strategy:
# We scan line-by-line.  Within each line we look for any sentence that CONTAINS
# a strong-claim signal.  A "sentence" here is any run of non-`.!?` characters
# ending at a sentence-terminal (or end-of-line).

# Patterns that fire on signal TOKENS within the full draft text.
# For each match we extract the surrounding sentence.

_QUANTIFIED_RE = re.compile(
    r"""
    (?:
        \d+\s*%                                   # 52%
      | \d+[\.,]\d+\s*%                           # 52.5%
      | \b\d+[xX]\b                               # 3x
      | \b\d+\s+(?:times|fold)\b                  # 2 times, 3-fold
      | \b\d+\s*(?:points?|percentile)\b          # 12 points, 95th percentile
      | \b\d+\s*weeks?\s+of\s+\w+                 # 8 weeks of growth
      | \bin\s+\d+\s*(?:minutes?|hours?|days?)\b  # in 20 minutes
      | \b\d+\s*(?:million|billion|thousand)\b    # 5 million
      | \b\d{4,}\b                                # large counts ≥ 1,000
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

_SUPERLATIVE_RE = re.compile(
    r"""
    \b(?:
        only           # "the only solution"
      | first(?:\s+ever)?  # "first ever"
      | best           # "best in class"
      | \#1            # "#1 rated"
      | number\s+one   # "number one"
      | most           # "most effective"
      | leading        # "leading platform"
      | industry[- ]leading
      | proven         # "proven results"
      | guaranteed     # "guaranteed growth"
      | unmatched      # "unmatched accuracy"
      | unparalleled
      | fastest        # "fastest path"
      | highest        # "highest rated"
      | superior       # "superior outcomes"
      | world[- ]class
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_COMPARATIVE_RE = re.compile(
    r"""
    \b(?:
        more\s+than
      | outperform      # outperforms, outperformed
      | vs\.
      | compared\s+to
      | better\s+than
      | ahead\s+of
      | beats?\b
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_ACTIVE_PATTERNS: dict[str, re.Pattern[str]] = {
    "quantified": _QUANTIFIED_RE,
    "superlative": _SUPERLATIVE_RE,
    "comparative": _COMPARATIVE_RE,
}

# Sentence splitter — splits at  .  !  ?  followed by whitespace/EOL.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class NearestApproved:
    id: int
    phrasing: str
    similarity: float


@dataclass
class ClaimFlag:
    start: int
    end: int
    text: str
    reason: str
    nearest_approved: list[NearestApproved] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Text normalisation (shared between candidate + approved phrasing)
# ─────────────────────────────────────────────────────────────────────────────

_PUNCT_TABLE = str.maketrans("", "", string.punctuation)


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return " ".join(text.lower().translate(_PUNCT_TABLE).split())


def _token_set(text: str) -> set[str]:
    """Return the word-token set from normalised text."""
    return set(_normalize(text).split())


# ─────────────────────────────────────────────────────────────────────────────
# Token-set similarity (deterministic, no cosine/embeddings)
# ─────────────────────────────────────────────────────────────────────────────
#
# We use a variant of Jaccard similarity over normalised token sets.
# Pure Jaccard is strict (short spans score low against long claims).
# We also compute the overlap / min(len(a), len(b)) ratio (a "containment"
# score) and return max(jaccard, containment).  This ensures that a short
# candidate like "Amira improves scores 99%" matches an approved claim phrasing
# of "Amira improves oral reading fluency scores by 25%" more reliably than
# pure Jaccard would.


def token_set_similarity(a: str, b: str) -> float:
    """Symmetric similarity in [0, 1] between two text strings."""
    ta = _token_set(a)
    tb = _token_set(b)
    if not ta or not tb:
        return 0.0
    intersection = len(ta & tb)
    union = len(ta | tb)
    jaccard = intersection / union if union else 0.0
    containment = intersection / min(len(ta), len(tb))
    return max(jaccard, containment)


# ─────────────────────────────────────────────────────────────────────────────
# Candidate extraction
# ─────────────────────────────────────────────────────────────────────────────


def _extract_sentence_containing(text: str, pos: int) -> tuple[int, int] | None:
    """Return (start, end) of the sentence that contains character position pos.

    Uses a simple heuristic: scan left to the previous .!? or start-of-line,
    then scan right to the next .!? or end-of-text.
    """
    # find sentence start
    start = pos
    while start > 0 and text[start - 1] not in ".!?\n":
        start -= 1
    # skip leading whitespace
    while start < len(text) and text[start] in " \t":
        start += 1

    # find sentence end
    end = pos
    while end < len(text) and text[end] not in ".!?\n":
        end += 1
    # include the terminal punctuation if present
    if end < len(text) and text[end] in ".!?":
        end += 1

    if end <= start:
        return None
    return (start, end)


def _extract_candidates(text: str) -> list[tuple[int, int, str, str]]:
    """Return list of (start, end, span_text, reason) for every candidate span.

    Overlapping spans are deduplicated (longest wins).
    Spans that are too short (length or word count) are discarded.
    """
    found: list[tuple[int, int, str, str]] = []

    for class_name, pattern in _ACTIVE_PATTERNS.items():
        if not PATTERN_CLASSES.get(class_name, False):
            continue
        for m in pattern.finditer(text):
            bounds = _extract_sentence_containing(text, m.start())
            if bounds is None:
                continue
            start, end = bounds
            span = text[start:end].strip()
            if len(span) < MIN_CANDIDATE_CHARS:
                continue
            if len(span.split()) < MIN_CANDIDATE_WORDS:
                continue
            found.append((start, end, span, class_name))

    if not found:
        return []

    # Deduplicate: sort by (start, -len); keep first non-overlapping.
    found.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    deduped: list[tuple[int, int, str, str]] = []
    last_end = -1
    for item in found:
        start, end, span, reason = item
        if start < last_end:
            # Overlapping with a previous (longer) span — skip.
            continue
        deduped.append(item)
        last_end = end

    return deduped


# ─────────────────────────────────────────────────────────────────────────────
# Main public function
# ─────────────────────────────────────────────────────────────────────────────


def scan_draft_for_flags(
    draft_text: str,
    approved_claims: list[tuple[int, str]],  # [(claim_id, approved_phrasing), …]
) -> list[ClaimFlag]:
    """Scan *draft_text* and return flagged spans.

    Args:
        draft_text:      The full plain-text draft to scan.
        approved_claims: List of (id, phrasing) for all APPROVED claims in the
                         profile.  Used for suppression + nearestApproved.

    Returns:
        List of ClaimFlag objects (start, end in character offsets of the
        original *draft_text*).  Candidates similar to approved claims are
        SUPPRESSED (not returned).
    """
    candidates = _extract_candidates(draft_text)
    flags: list[ClaimFlag] = []

    for start, end, span_text, reason in candidates:
        # Compute similarity against all approved claims.
        scored: list[tuple[float, int, str]] = []  # (score, claim_id, phrasing)
        for claim_id, phrasing in approved_claims:
            score = token_set_similarity(span_text, phrasing)
            scored.append((score, claim_id, phrasing))

        scored.sort(key=lambda x: -x[0])

        best_score = scored[0][0] if scored else 0.0

        # Suppress if above threshold.
        if best_score >= SUPPRESS_THRESHOLD:
            continue

        # Build nearestApproved context (top MAX_NEAREST).
        nearest = [
            NearestApproved(id=cid, phrasing=ph, similarity=round(sc, 3))
            for sc, cid, ph in scored[:MAX_NEAREST]
            if sc > 0.0
        ]

        flags.append(
            ClaimFlag(
                start=start,
                end=end,
                text=span_text,
                reason=reason,
                nearest_approved=nearest,
            )
        )

    return flags
