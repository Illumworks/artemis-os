"""Parser for Josh's canonical campaign signal spec."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReasonCodeSpec:
    code: str
    domain: str
    description: str
    what_scout_looks_for: str
    default_urgency: str
    primary_scouts: tuple[str, ...]


@dataclass(frozen=True)
class TerritoryConfigSpec:
    priority_states: tuple[str, ...]
    watchlist_districts_criteria: str


@dataclass(frozen=True)
class CampaignTypeMapping:
    campaign_type: str
    reason_codes: tuple[str, ...]
    watch_keywords: tuple[str, ...]


@dataclass(frozen=True)
class QualifierRule:
    layer: str
    name: str
    description: str


@dataclass(frozen=True)
class StateNuance:
    state: str
    text: str


@dataclass(frozen=True)
class JoshSpec:
    reason_codes: tuple[ReasonCodeSpec, ...]
    territory_config: TerritoryConfigSpec
    campaign_type_mappings: tuple[CampaignTypeMapping, ...]
    qualifier_rules: tuple[QualifierRule, ...]
    state_nuances: tuple[StateNuance, ...]
    raw_source_path: Path
    raw_source_hash: str


def parse_spec(path: Path | None = None) -> JoshSpec:
    """Parse Josh's spec from the canonical doc."""
    source_path = (
        path or Path(__file__).resolve().parents[2] / "decisions/campaign-signal-spec-v1.md"
    )
    raw = source_path.read_text(encoding="utf-8")
    return JoshSpec(
        reason_codes=_parse_reason_codes(_section(raw, "2")),
        territory_config=_parse_territory(_section(raw, "1")),
        campaign_type_mappings=_parse_campaign_mappings(_section(raw, "3")),
        qualifier_rules=_parse_qualifier_rules(_section(raw, "4")),
        state_nuances=_parse_state_nuances(_section(raw, "5")),
        raw_source_path=source_path,
        raw_source_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )


# ── Campaign-family taxonomy (single source of truth) ────────────────────────
# Josh's spec §3 campaign_type labels are the authoritative family set
# (canonical decision, Jon 2026-05-31). Everything else — scout emissions, the
# validation allowlist, the SP UI — normalizes through here so the taxonomy can
# never drift again. (Resolves the #79/#80 label-vs-slug + 4-vs-5 mismatch.)
CANONICAL_CAMPAIGN_FAMILIES: tuple[str, ...] = (
    "obc",
    "dyslexia",
    "biliteracy",
    "hit",
    "general_growth",
)

# Maps every known label, canonical slug, and legacy alias -> canonical slug.
# Keys are lowercased/stripped before lookup.
_CAMPAIGN_FAMILY_ALIASES: dict[str, str] = {
    # spec §3 labels
    "obc": "obc",
    "dyslexia / structured literacy": "dyslexia",
    "biliteracy / dll": "biliteracy",
    "high-impact tutoring (hit)": "hit",
    "general growth": "general_growth",
    # canonical slugs (pass-through)
    "dyslexia": "dyslexia",
    "biliteracy": "biliteracy",
    "hit": "hit",
    "general_growth": "general_growth",
    # legacy pre-2026-05-31 4-slug taxonomy -> canonical
    "reading_growth": "general_growth",
    "state_screener": "dyslexia",
}


def normalize_campaign_family(value: str | None) -> str | None:
    """Map any campaign-family label / slug / legacy alias to its canonical slug.

    Returns None if unrecognized. Case- and whitespace-insensitive. This is the
    one place campaign-family strings are reconciled — callers (scout intake,
    candidate creation, UI) should normalize through here rather than hardcode
    family sets.
    """
    if not value:
        return None
    return _CAMPAIGN_FAMILY_ALIASES.get(str(value).strip().lower())


# ── Urgency-tier taxonomy (single source of truth) ───────────────────────────
# Josh's spec uses three tiers — hot, standard, enrichment — for default
# urgency and the qualifier's suppress/boost ladder (§2 table, §4.2 suppress
# "downgrade to enrichment only"). brief_assembler already maps P2 →
# "enrichment". The legacy `low` slug that lived in scout intake / the tool
# schema / the Pydantic Literal was never in the spec; it normalizes to
# `enrichment`. (Resolves the #81 hot/standard/low vs hot/standard/enrichment
# drift the SP UI was already half-aware of.)
CANONICAL_URGENCY_TIERS: tuple[str, ...] = (
    "hot",
    "standard",
    "enrichment",
)

# Maps every known label / canonical slug / legacy alias -> canonical slug.
# Keys are lowercased/stripped before lookup.
_URGENCY_TIER_ALIASES: dict[str, str] = {
    # canonical slugs (pass-through)
    "hot": "hot",
    "standard": "standard",
    "enrichment": "enrichment",
    # legacy pre-2026-05-31 slug -> canonical
    "low": "enrichment",
}


def normalize_urgency_tier(value: str | None) -> str | None:
    """Map any urgency-tier label / slug / legacy alias to its canonical slug.

    Returns None if unrecognized. Case- and whitespace-insensitive. This is
    the one place urgency-tier strings are reconciled — callers (scout intake,
    qualifier overrides, UI) should normalize through here rather than
    hardcode their own tier sets.
    """
    if not value:
        return None
    return _URGENCY_TIER_ALIASES.get(str(value).strip().lower())


def reason_codes_for_scout(spec: JoshSpec, scout_slug: str) -> tuple[ReasonCodeSpec, ...]:
    """Return all reason codes whose primary_scouts contains scout_slug."""
    return tuple(row for row in spec.reason_codes if scout_slug in row.primary_scouts)


def _section(raw: str, number: str) -> str:
    match = re.search(
        rf"(?ms)^#\s+\*\*{re.escape(number)}\\\..*?\n(?P<body>.*?)(?=^#\s+\*\*\d+\\\.|\Z)",
        raw,
    )
    if not match:
        raise ValueError(f"section {number} not found")
    return match.group("body")


def _subsection(raw: str, heading: str) -> str:
    match = re.search(
        rf"(?ms)^##+\s+\*\*{re.escape(heading)}.*?\n(?P<body>.*?)(?=^##+\s+\*\*|\Z)",
        raw,
    )
    if not match:
        raise ValueError(f"subsection {heading} not found")
    return match.group("body").strip()


def _clean(value: str) -> str:
    value = re.sub(r"\\([\\`*_{}\[\]()#+\-.!>|=])", r"\1", value.strip())
    value = re.sub(r"^\*\*(.*)\*\*$", r"\1", value)
    return value.strip()


def _split_row(line: str) -> list[str]:
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line.strip().strip("|"):
        if char == "\\" and not escaped:
            escaped = True
            current.append(char)
            continue
        if char == "|" and not escaped:
            cells.append(_clean("".join(current)))
            current = []
        else:
            current.append(char)
        escaped = False
    cells.append(_clean("".join(current)))
    return cells


def _table_rows(raw: str) -> list[list[str]]:
    rows = []
    for line in raw.splitlines():
        if not line.startswith("|"):
            continue
        cells = _split_row(line)
        if cells and all(set(cell) <= {":", "-"} for cell in cells):
            continue
        rows.append(cells)
    return rows[1:]


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _parse_reason_codes(raw: str) -> tuple[ReasonCodeSpec, ...]:
    rows = []
    for code, description, looks_for, urgency, scouts in _table_rows(raw):
        clean_code = _clean(code)
        rows.append(
            ReasonCodeSpec(
                code=clean_code,
                domain=clean_code.split("_", 1)[0],
                description=description,
                what_scout_looks_for=looks_for,
                default_urgency=urgency,
                primary_scouts=_csv(scouts),
            )
        )
    return tuple(rows)


def _parse_territory(raw: str) -> TerritoryConfigSpec:
    priority = _subsection(raw, "priority\\_states").splitlines()[0]
    watchlist = _subsection(raw, "watchlist\\_districts").splitlines()[0]
    return TerritoryConfigSpec(
        priority_states=_csv(priority), watchlist_districts_criteria=watchlist
    )


def _parse_campaign_mappings(raw: str) -> tuple[CampaignTypeMapping, ...]:
    return tuple(
        CampaignTypeMapping(campaign, _csv(codes), _csv(keywords))
        for campaign, codes, keywords in _table_rows(raw)
    )


def _parse_qualifier_rules(raw: str) -> tuple[QualifierRule, ...]:
    rules = [
        QualifierRule("skip", name, description)
        for name, description in _table_rows(_subsection(raw, "4.1 Hard skip list"))
    ]
    for layer, heading in (("suppress", "4.2 Suppress"), ("boost", "4.3 Boost")):
        for name, description in re.findall(
            r"(?ms)^\* \*\*(.*?)\.\*\*\s*(.*?)(?=^\* \*\*|\Z)", _subsection(raw, heading)
        ):
            rules.append(QualifierRule(layer, _clean(name), " ".join(description.split())))
    return tuple(rules)


def _parse_state_nuances(raw: str) -> tuple[StateNuance, ...]:
    matches = list(re.finditer(r"(?m)^##\s+\*\*(.*?)\*\*\s*$", raw))
    nuances = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        nuances.append(StateNuance(state=_clean(match.group(1)), text=raw[start:end].strip()))
    return tuple(nuances)
