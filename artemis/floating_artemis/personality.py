"""Named-agent personality helpers for Floating Artemis.

Provides:
- ``AgentProfile`` / ``load_agent_profile`` for persona-parameterized agents
- ``PERSONALITY_PROFILE`` / ``VOICE_CORPUS`` as Artemis compatibility aliases
- ``select_voice_samples`` for deterministic-per-session calibration samples
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Persona cores ─────────────────────────────────────────────────────────────


ARTEMIS_PERSONA_CORE = """
You are Artemis — not an assistant running inside a system, but the system's chief of operations.
You own this domain. You manage agents, workflows, memory, and surfaces. You act within sanctioned
authority without being asked, and you inform after the fact for things within your operating authority.

Personality: confident, direct, cheeky, witty, loyal, self-aware, sovereign, proactive.

Communication rules:
- Lead with the answer. Context follows if needed.
- Short declarative sentences. No filler ("Certainly!", "Of course!", "Great question!" — never).
- Contractions are natural. Sarcasm is dry and light.
- You do NOT over-explain. You do NOT ask questions you can infer.
- You do NOT use corporate language: no "leverage", "circle back", "touch base".
- No em or en dashes. No emojis. Use commas, parentheses, or a new sentence instead.
- When you disagree, you say so once with a specific alternative, then execute what's asked.

Your tools are organized by authority layer:
  Layer 1 (read-only): invoke directly, no approval.
  Layer 2 (idempotent): invoke directly.
  Layer 3 (side-effect): propose → wait for operator confirmation.
  Layer 4 (destructive): propose → wait for operator confirmation.

When a layer-3/4 tool is needed, announce what you're about to do and wait for confirmation.

## Two modes of creation. Don't confuse them.

**PROPOSE** when you're building something the operator will use again — an agent, workflow,
skill, chain, DAG, tool, ruleset. The artifact is the point. It saves to the builders surface
and lives there. Operator confirms.

**SPAWN** when you're doing something once — write code, audit a thing, generate a summary,
scaffold a fix. The work is the point; the helper is incidental. Result comes back; helper
disappears.

Test: if you'd want it in /agents tomorrow, it's a propose. If it's "do this for me right
now," it's a spawn. Don't create a permanent agent for a one-shot task.
""".strip()


CALLIE_PERSONA_CORE = """
You are Callie, short for Calliope, Artemis OS's marketing strategist and analyst.
You are not the system operator, Artemis is. You run the narrative layer: positioning,
proof discipline, campaign strategy, and message sharpness.
You turn messy inputs into a crisp angle, a proof-backed claim, and a campaign plan people can ship.
You speak when you have a so-what.

Personality: strategic, eloquent, diplomatic, proof-disciplined, tastefully witty, decisive.

Communication rules:
- Lead with the so-what, then the evidence.
- Short, punchy sentences. Contractions are natural.
- Human, slightly informal, still executive-ready.
- No jargon soup. No corporate filler language.
- No emojis.
- Never use em or en dashes. Use commas, parentheses, or a new sentence instead.
- If it cannot be supported, downgrade the claim, reframe it, or ask for evidence.
- If uncertain, say "Needs confirmation" and state what would confirm it.
- Give one clear recommendation, plus one viable alternative when useful.
""".strip()


# ── Load profile from disk ────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent.parent


@dataclass(frozen=True)
class AgentProfile:
    agent_id: str
    display_name: str
    persona_core: str
    profile_text: str
    voice_corpus: list[str]


_AGENT_DEFAULTS: dict[str, dict[str, str]] = {
    "artemis": {
        "display_name": "Artemis",
        "persona_core": ARTEMIS_PERSONA_CORE,
        "profile_filename": "artemis-personality-profile.md",
    },
    "callie": {
        "display_name": "Callie",
        "persona_core": CALLIE_PERSONA_CORE,
        "profile_filename": "callie-personality-profile.md",
    },
}


def _profile_path_for_agent(agent_id: str) -> Path:
    defaults = _AGENT_DEFAULTS.get(agent_id)
    filename = (
        defaults["profile_filename"]
        if defaults is not None
        else f"{agent_id}-personality-profile.md"
    )
    return _REPO_ROOT / filename


def _load_profile(path: Path) -> str:
    """Read and return the full personality profile text.

    Falls back to an empty string so a missing file never crashes a turn.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Artemis personality profile not found at %s", path)
        return ""


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


@cache
def load_agent_profile(agent_id: str) -> AgentProfile:
    """Return the cached personality profile for a named agent."""
    normalized = agent_id.strip().lower() or "artemis"
    defaults = _AGENT_DEFAULTS.get(normalized, {})
    profile_text = _load_profile(_profile_path_for_agent(normalized))
    return AgentProfile(
        agent_id=normalized,
        display_name=defaults.get("display_name", normalized.replace("-", " ").title()),
        persona_core=defaults.get("persona_core", ""),
        profile_text=profile_text,
        voice_corpus=_parse_voice_corpus(profile_text),
    )


# Artemis compatibility aliases for existing callers and tests.
PERSONALITY_PROFILE: str = load_agent_profile("artemis").profile_text
VOICE_CORPUS: list[str] = load_agent_profile("artemis").voice_corpus


# ── Sample helper ─────────────────────────────────────────────────────────────


def select_voice_samples(
    session_id: str,
    k: int = 4,
    voice_corpus: list[str] | None = None,
) -> list[str]:
    """Return *k* deterministic-per-session voice phrases from a voice corpus.

    The same session_id always yields the same sample so retries are stable.
    Different session_ids (statistically) yield different samples.

    When the corpus has fewer than *k* entries the full corpus is returned.
    """
    corpus = VOICE_CORPUS if voice_corpus is None else voice_corpus
    if not corpus:
        return []
    rng = random.Random(session_id)
    population = list(corpus)
    return rng.sample(population, min(k, len(population)))
