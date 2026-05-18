"""Artemis personality profile — loaded once at module import.

Provides:
- _PERSONALITY_PROFILE : full text of artemis-personality-profile.md
- VOICE_CORPUS         : list of characteristic phrases parsed from the profile
- select_voice_samples : deterministic-per-session random sample of VOICE_CORPUS
"""

from __future__ import annotations

import logging
import random
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Load profile from disk ────────────────────────────────────────────────────

# The profile lives at the repo root alongside this package tree.
_PROFILE_PATH = Path(__file__).parent.parent.parent / "artemis-personality-profile.md"


def _load_profile(path: Path = _PROFILE_PATH) -> str:
    """Read and return the full personality profile text.

    Falls back to an empty string so a missing file never crashes a turn.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Artemis personality profile not found at %s", path)
        return ""


PERSONALITY_PROFILE: str = _load_profile()


# ── Parse voice corpus ────────────────────────────────────────────────────────

_VOICE_SECTION_RE = re.compile(
    r"\*\*Characteristic phrases[^*]*\*\*.*?\n(.*?)(?:\n\n|\Z)",
    re.DOTALL,
)
_PHRASE_LINE_RE = re.compile(r'^- "(.+)"', re.MULTILINE)


def _parse_voice_corpus(profile_text: str) -> list[str]:
    """Extract quoted characteristic phrases from the profile.

    Looks for the '**Characteristic phrases …**' section and pulls every
    ``- "…"`` bullet from it.  Returns an empty list if the section is absent
    or malformed — never raises.
    """
    if not profile_text:
        return []
    match = _VOICE_SECTION_RE.search(profile_text)
    if not match:
        # Fallback: scan the whole document for bullet-quoted phrases
        return _PHRASE_LINE_RE.findall(profile_text)
    section_text = match.group(1)
    return _PHRASE_LINE_RE.findall(section_text)


VOICE_CORPUS: list[str] = _parse_voice_corpus(PERSONALITY_PROFILE)


# ── Sample helper ─────────────────────────────────────────────────────────────


def select_voice_samples(session_id: str, k: int = 4) -> list[str]:
    """Return *k* deterministic-per-session voice phrases from VOICE_CORPUS.

    The same session_id always yields the same sample so retries are stable.
    Different session_ids (statistically) yield different samples.

    When the corpus has fewer than *k* entries the full corpus is returned.
    """
    if not VOICE_CORPUS:
        return []
    rng = random.Random(session_id)
    population = list(VOICE_CORPUS)
    return rng.sample(population, min(k, len(population)))
