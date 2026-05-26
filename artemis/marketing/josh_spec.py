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
