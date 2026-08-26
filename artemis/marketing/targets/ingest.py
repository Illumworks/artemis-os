"""Load a target-account list from a file Josh posts.

Deliberately reuses ``artemis.files`` rather than re-parsing: the list arrives as
a Slack upload, which is exactly what that layer exists to read. Josh will post
revised lists, so this is re-runnable and idempotent on
``(state, account_name)``.

**Replace, do not merge.** A re-import marks accounts absent from the new file as
departed rather than leaving them behind. A district that drops off Josh's list
has usually become a customer -- the single thing he most wants excluded -- and
a merge would keep targeting it forever.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.targets.matching import normalize_district_name
from artemis.marketing.targets.models import TargetAccount

logger = logging.getLogger(__name__)

# Column headers as Salesforce exports them. Matched case- and space-insensitively
# so a re-export with cosmetic differences does not silently import nothing.
_REQUIRED = ("account name", "billing state/province")
_ALIASES: dict[str, tuple[str, ...]] = {
    "account_name": ("account name",),
    "state": ("billing state/province", "state"),
    "marketing_tier": ("district marketing tier", "marketing tier", "tier"),
    "enrollment": ("enrollment in district", "enrollment"),
    "sales_owner": ("sales", "sales owner", "owner"),
    "channel_partner": ("amira channel partner", "channel partner"),
    "is_customer": ("is customer",),
    "is_parent_account": ("is parent account",),
}


class TargetListError(Exception):
    """The file is not a usable target list. Carries a repeatable reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class ImportReport:
    """What an import actually did — the effect, not the intent."""

    total_rows: int = 0
    inserted: int = 0
    updated: int = 0
    departed: int = 0
    skipped: list[str] = field(default_factory=list)
    unnormalizable: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = [
            f"{self.total_rows:,} rows read",
            f"{self.inserted:,} new",
            f"{self.updated:,} updated",
        ]
        if self.departed:
            bits.append(f"{self.departed:,} no longer on the list")
        if self.skipped:
            bits.append(f"{len(self.skipped)} skipped")
        return ", ".join(bits)


def _headers(fieldnames: Sequence[str] | None) -> dict[str, str]:
    """Map our field names onto the file's actual headers."""
    present = {(h or "").strip().lower(): (h or "") for h in (fieldnames or [])}
    missing = [r for r in _REQUIRED if r not in present]
    if missing:
        raise TargetListError(
            "That file does not look like a target-account list -- it is missing the "
            f"{' and '.join(repr(m) for m in missing)} column(s). Expected a Salesforce "
            "export with at least 'Account Name' and 'Billing State/Province'."
        )
    resolved: dict[str, str] = {}
    for field_name, options in _ALIASES.items():
        for option in options:
            if option in present:
                resolved[field_name] = present[option]
                break
    return resolved


def _flag(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y"}


def _int_or_none(value: str) -> int | None:
    raw = (value or "").strip().replace(",", "")
    return int(raw) if raw.isdigit() else None


async def import_target_accounts(
    session: AsyncSession,
    payload: str,
    *,
    source_file_id: str = "",
    imported_by: str = "",
    delimiter: str = "\t",
) -> ImportReport:
    """Import a target list, replacing what was there.

    Raises ``TargetListError`` when the file is not a target list at all -- a
    wrong file must fail loudly, not quietly wipe the universe Josh sells into.
    """
    reader = csv.DictReader(io.StringIO(payload), delimiter=delimiter)
    columns = _headers(reader.fieldnames)

    existing = {
        (row.state, row.account_name): row
        for row in (await session.execute(select(TargetAccount))).scalars().all()
    }
    seen: set[tuple[str, str]] = set()
    report = ImportReport()

    for raw in reader:
        report.total_rows += 1
        name = (raw.get(columns["account_name"]) or "").strip()
        state = (raw.get(columns["state"]) or "").strip().upper()
        if not name or not state:
            report.skipped.append(f"row {report.total_rows}: missing account name or state")
            continue

        normalized = normalize_district_name(name)
        if not normalized:
            # Not an error: a real account ("Community Independent School
            # District", TX) is entirely generic words. It is stored and
            # matchable by EXACT name; it just has no normalized fallback.
            report.unnormalizable.append(f"{name} ({state})")

        key = (state, name)
        seen.add(key)
        values = {
            "normalized_name": normalized or None,
            "marketing_tier": (raw.get(columns.get("marketing_tier", "")) or "").strip() or None,
            "enrollment": _int_or_none(raw.get(columns.get("enrollment", "")) or ""),
            "sales_owner": (raw.get(columns.get("sales_owner", "")) or "").strip() or None,
            "channel_partner": (raw.get(columns.get("channel_partner", "")) or "").strip() or None,
            "is_customer": _flag(raw.get(columns.get("is_customer", "")) or ""),
            "is_parent_account": _flag(raw.get(columns.get("is_parent_account", "")) or "1"),
            "source_file_id": source_file_id or None,
            "imported_by": imported_by or None,
        }

        row = existing.get(key)
        if row is None:
            session.add(TargetAccount(account_name=name, state=state, **values))
            report.inserted += 1
        else:
            for attr, value in values.items():
                setattr(row, attr, value)
            report.updated += 1

    if not report.total_rows:
        raise TargetListError(
            "That file has the right columns but no data rows, so nothing was imported."
        )

    # Departed accounts. Marked, never deleted: a district that leaves the list
    # has usually become a customer, and that is exactly what Josh needs excluded
    # -- but the history of having targeted it stays.
    for key, row in existing.items():
        if key not in seen and row.match_method != "departed":
            row.match_method = "departed"
            report.departed += 1

    await session.flush()
    logger.info("target_accounts: import complete -- %s", report.summary())
    return report


async def resolve_target_districts(session: AsyncSession) -> dict[str, int]:
    """Link target accounts to NCES districts where it can be done safely.

    Records HOW each link was made, or why it was not, in ``match_method``:

    ``exact``                      name and state matched outright
    ``normalized``                 matched a single district on the reduced name
    ``abstained_ambiguous``        the reduced name matched more than one district
    ``abstained_no_match``         nothing matched
    ``abstained_unnormalizable``   the name is entirely generic words

    The abstain cases are the point. Salesforce and NCES disagree often enough
    ("Sweetwater Union School District" vs "Sweetwater Union High") that roughly
    a fifth of the list will not resolve, and a unique match against an
    incomplete table is exactly how a district ends up confidently mis-assigned.
    An unresolved row is still a perfectly good target -- it is matched by name
    instead -- so abstaining costs nothing and guessing costs a real account.
    """
    from artemis.marketing.models import District

    districts = (await session.execute(select(District))).scalars().all()

    by_exact: dict[tuple[str, str], list[int]] = {}
    by_normalized: dict[tuple[str, str], list[int]] = {}
    for district in districts:
        if not district.name or not district.state:
            continue
        state = district.state.upper()
        by_exact.setdefault((state, district.name.upper().strip()), []).append(district.id)
        key = normalize_district_name(district.name)
        if key:
            by_normalized.setdefault((state, key), []).append(district.id)

    counts: dict[str, int] = {}
    targets = (await session.execute(select(TargetAccount))).scalars().all()
    for target in targets:
        if target.match_method == "departed":
            continue
        state = target.state.upper()

        exact = by_exact.get((state, target.account_name.upper().strip()))
        if exact and len(exact) == 1:
            target.district_id, target.match_method = exact[0], "exact"
        elif not target.normalized_name:
            target.district_id, target.match_method = None, "abstained_unnormalizable"
        else:
            hits = by_normalized.get((state, target.normalized_name), [])
            if len(hits) == 1:
                target.district_id, target.match_method = hits[0], "normalized"
            elif len(hits) > 1:
                target.district_id, target.match_method = None, "abstained_ambiguous"
            else:
                target.district_id, target.match_method = None, "abstained_no_match"

        counts[target.match_method or "?"] = counts.get(target.match_method or "?", 0) + 1

    await session.flush()
    logger.info("target_accounts: district resolution -- %s", counts)
    return counts
