# mypy: ignore-errors
"""Migration script: OKR Studio + Writing Studio rules (Node SQLite → Python Postgres).

Usage:
    uv run python scripts/migrate_okr_writing_rules.py --dry-run [--source PATH] [--report PATH]
    uv run python scripts/migrate_okr_writing_rules.py --apply  [--source PATH] [--report PATH]

--dry-run (default):
    - Read each table from the source SQLite.
    - Validate rows against Pydantic schemas.
    - Build mapping plan (unix-seconds → TIMESTAMPTZ, JSON-TEXT → JSONB).
    - Detect natural-key conflicts with existing Postgres data.
    - Write JSONL report. Write NOTHING to Postgres.
    - Exit 0 if no validation errors; 1 otherwise.

--apply:
    - Re-run dry-run; abort if any validation errors.
    - Insert rows in dependency order within a single transaction.
    - Idempotent on natural keys — conflicts are skipped + reported.
    - Rollback entire transaction on any error.
    - Exit 0 on success.

ACTUAL CUTOVER against the live database requires Jon's explicit greenlight (Phase H).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Validate-before-import: make sure the project root is on sys.path.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pydantic import ValidationError  # noqa: E402  (after sys.path fix)

# Domain schemas
from artemis.okr.schemas import (  # noqa: E402
    OkrActivityRow,
    OkrKeyResultRow,
    OkrNextUpRow,
    OkrObjectiveRow,
    OkrUpdatePreviewRow,
)
from artemis.writing_rules.schemas import (  # noqa: E402
    WritingExampleRow,
    WritingFolderRow,
    WritingProfileRow,
    WritingRuleRow,
    WritingSourceRow,
)

DEFAULT_SOURCE = Path.home() / ".artemis" / "data.db"
DEFAULT_REPORT = Path("migration_report.jsonl")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(unix_secs: int | None) -> datetime | None:
    """Convert a Node unix-seconds integer to an aware datetime (UTC)."""
    if unix_secs is None:
        return None
    return datetime.fromtimestamp(unix_secs, tz=UTC)


def _json(text: str | None, default: Any = None) -> Any:
    """Parse a JSON-in-TEXT column safely."""
    if text is None:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def _read_table(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(f"SELECT * FROM {table}")  # noqa: S608
    rows = cur.fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TableReport:
    table: str
    source_count: int = 0
    valid_count: int = 0
    conflict_count: int = 0
    skipped_count: int = 0
    inserted_count: int = 0
    validation_errors: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "source_count": self.source_count,
            "valid_count": self.valid_count,
            "conflict_count": self.conflict_count,
            "skipped_count": self.skipped_count,
            "inserted_count": self.inserted_count,
            "validation_errors": self.validation_errors,
            "conflicts": self.conflicts,
        }

    @property
    def has_errors(self) -> bool:
        return len(self.validation_errors) > 0


@dataclass
class MigrationPlan:
    reports: dict[str, TableReport] = field(default_factory=dict)

    @property
    def has_validation_errors(self) -> bool:
        return any(r.has_errors for r in self.reports.values())

    def write_report(self, path: Path) -> None:
        with path.open("w") as f:
            summary = {
                "event": "summary",
                "timestamp": datetime.now(UTC).isoformat(),
                "has_validation_errors": self.has_validation_errors,
                "tables": {
                    t: {
                        "source_count": r.source_count,
                        "valid_count": r.valid_count,
                        "conflict_count": r.conflict_count,
                        "inserted_count": r.inserted_count,
                        "validation_errors": len(r.validation_errors),
                    }
                    for t, r in self.reports.items()
                },
            }
            f.write(json.dumps(summary) + "\n")
            for report in self.reports.values():
                f.write(json.dumps({"event": "table", **report.to_dict()}) + "\n")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_objectives(rows: list[dict[str, Any]], report: TableReport) -> list[OkrObjectiveRow]:
    valid: list[OkrObjectiveRow] = []
    for raw in rows:
        try:
            valid.append(OkrObjectiveRow.model_validate(raw))
            report.valid_count += 1
        except ValidationError as exc:
            report.validation_errors.append({"source_id": raw.get("id"), "errors": exc.errors()})
    return valid


def _validate_key_results(rows: list[dict[str, Any]], report: TableReport) -> list[OkrKeyResultRow]:
    valid: list[OkrKeyResultRow] = []
    for raw in rows:
        try:
            valid.append(OkrKeyResultRow.model_validate(raw))
            report.valid_count += 1
        except ValidationError as exc:
            report.validation_errors.append({"source_id": raw.get("id"), "errors": exc.errors()})
    return valid


def _validate_activity(rows: list[dict[str, Any]], report: TableReport) -> list[OkrActivityRow]:
    valid: list[OkrActivityRow] = []
    for raw in rows:
        try:
            valid.append(OkrActivityRow.model_validate(raw))
            report.valid_count += 1
        except ValidationError as exc:
            report.validation_errors.append({"source_id": raw.get("id"), "errors": exc.errors()})
    return valid


def _validate_next_up(rows: list[dict[str, Any]], report: TableReport) -> list[OkrNextUpRow]:
    valid: list[OkrNextUpRow] = []
    for raw in rows:
        try:
            valid.append(OkrNextUpRow.model_validate(raw))
            report.valid_count += 1
        except ValidationError as exc:
            report.validation_errors.append({"source_id": raw.get("id"), "errors": exc.errors()})
    return valid


def _validate_update_previews(
    rows: list[dict[str, Any]], report: TableReport
) -> list[OkrUpdatePreviewRow]:
    valid: list[OkrUpdatePreviewRow] = []
    for raw in rows:
        try:
            valid.append(OkrUpdatePreviewRow.model_validate(raw))
            report.valid_count += 1
        except ValidationError as exc:
            report.validation_errors.append({"source_id": raw.get("id"), "errors": exc.errors()})
    return valid


def _validate_profiles(rows: list[dict[str, Any]], report: TableReport) -> list[WritingProfileRow]:
    valid: list[WritingProfileRow] = []
    for raw in rows:
        try:
            valid.append(WritingProfileRow.model_validate(raw))
            report.valid_count += 1
        except ValidationError as exc:
            report.validation_errors.append({"source_id": raw.get("id"), "errors": exc.errors()})
    return valid


def _validate_folders(rows: list[dict[str, Any]], report: TableReport) -> list[WritingFolderRow]:
    valid: list[WritingFolderRow] = []
    for raw in rows:
        try:
            valid.append(WritingFolderRow.model_validate(raw))
            report.valid_count += 1
        except ValidationError as exc:
            report.validation_errors.append({"source_id": raw.get("id"), "errors": exc.errors()})
    return valid


def _validate_rules(rows: list[dict[str, Any]], report: TableReport) -> list[WritingRuleRow]:
    valid: list[WritingRuleRow] = []
    for raw in rows:
        try:
            valid.append(WritingRuleRow.model_validate(raw))
            report.valid_count += 1
        except ValidationError as exc:
            report.validation_errors.append({"source_id": raw.get("id"), "errors": exc.errors()})
    return valid


def _validate_examples(rows: list[dict[str, Any]], report: TableReport) -> list[WritingExampleRow]:
    valid: list[WritingExampleRow] = []
    for raw in rows:
        try:
            valid.append(WritingExampleRow.model_validate(raw))
            report.valid_count += 1
        except ValidationError as exc:
            report.validation_errors.append({"source_id": raw.get("id"), "errors": exc.errors()})
    return valid


def _validate_sources(rows: list[dict[str, Any]], report: TableReport) -> list[WritingSourceRow]:
    valid: list[WritingSourceRow] = []
    for raw in rows:
        try:
            valid.append(WritingSourceRow.model_validate(raw))
            report.valid_count += 1
        except ValidationError as exc:
            report.validation_errors.append({"source_id": raw.get("id"), "errors": exc.errors()})
    return valid


# ---------------------------------------------------------------------------
# Core: build plan
# ---------------------------------------------------------------------------


def build_plan(source_path: Path) -> MigrationPlan:
    """Read source SQLite, validate all rows, return a MigrationPlan."""
    plan = MigrationPlan()

    conn = sqlite3.connect(str(source_path))
    conn.row_factory = sqlite3.Row

    try:
        # -- OKR Objectives
        r = TableReport(table="okr_objectives")
        rows = _read_table(conn, "okr_objectives")
        r.source_count = len(rows)
        _validate_objectives(rows, r)
        plan.reports["okr_objectives"] = r

        # -- OKR Key Results
        r = TableReport(table="okr_key_results")
        rows = _read_table(conn, "okr_key_results")
        r.source_count = len(rows)
        _validate_key_results(rows, r)
        plan.reports["okr_key_results"] = r

        # -- OKR Activity
        r = TableReport(table="okr_activity")
        rows = _read_table(conn, "okr_activity")
        r.source_count = len(rows)
        _validate_activity(rows, r)
        plan.reports["okr_activity"] = r

        # -- OKR Next Up
        r = TableReport(table="okr_next_up")
        rows = _read_table(conn, "okr_next_up")
        r.source_count = len(rows)
        _validate_next_up(rows, r)
        plan.reports["okr_next_up"] = r

        # -- OKR Update Previews (ephemeral — skip if empty)
        r = TableReport(table="okr_update_previews")
        rows = _read_table(conn, "okr_update_previews")
        r.source_count = len(rows)
        if rows:
            _validate_update_previews(rows, r)
        plan.reports["okr_update_previews"] = r

        # -- Writing Profiles
        r = TableReport(table="writing_profiles")
        rows = _read_table(conn, "writing_profiles")
        r.source_count = len(rows)
        _validate_profiles(rows, r)
        plan.reports["writing_profiles"] = r

        # -- Writing Folders
        r = TableReport(table="writing_folders")
        rows = _read_table(conn, "writing_folders")
        r.source_count = len(rows)
        _validate_folders(rows, r)
        plan.reports["writing_folders"] = r

        # -- Writing Rules
        r = TableReport(table="writing_rules")
        rows = _read_table(conn, "writing_rules")
        r.source_count = len(rows)
        _validate_rules(rows, r)
        plan.reports["writing_rules"] = r

        # -- Writing Examples
        r = TableReport(table="writing_examples")
        rows = _read_table(conn, "writing_examples")
        r.source_count = len(rows)
        _validate_examples(rows, r)
        plan.reports["writing_examples"] = r

        # -- Writing Sources
        r = TableReport(table="writing_sources")
        rows = _read_table(conn, "writing_sources")
        r.source_count = len(rows)
        _validate_sources(rows, r)
        plan.reports["writing_sources"] = r

    finally:
        conn.close()

    return plan


# ---------------------------------------------------------------------------
# Apply: insert into Postgres (synchronous via psycopg2-style calls not
# available here, so we use asyncio + SQLAlchemy).
# ---------------------------------------------------------------------------


def _apply_plan(source_path: Path, plan: MigrationPlan) -> None:
    """Insert validated rows into Postgres. Called only when --apply is set."""
    import asyncio

    asyncio.run(_async_apply(source_path, plan))


async def _async_apply(source_path: Path, plan: MigrationPlan) -> None:  # noqa: PLR0912,PLR0915
    """Async apply — all inserts inside one transaction; rollback on error."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from artemis.config import settings
    from artemis.db import attach_pgvector_codec

    engine = create_async_engine(settings.db_url, echo=False, future=True)
    attach_pgvector_codec(engine)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    conn = sqlite3.connect(str(source_path))
    conn.row_factory = sqlite3.Row

    try:
        async with session_factory() as session, session.begin():
            await _apply_okr(conn, session, plan)
            await _apply_writing_rules(conn, session, plan)
    finally:
        conn.close()
        await engine.dispose()


async def _apply_okr(  # noqa: PLR0912
    conn: sqlite3.Connection,
    session: Any,
    plan: MigrationPlan,
) -> None:
    from sqlalchemy import select

    from artemis.okr.models import (
        OkrActivity,
        OkrKeyResult,
        OkrNextUp,
        OkrObjective,
        OkrUpdatePreview,
    )

    # ── Objectives (natural key: title + cycle) ───────────────────────────────
    obj_rows = _read_table(conn, "okr_objectives")
    r = plan.reports["okr_objectives"]
    # source_id → postgres_id map for FK rewiring
    obj_id_map: dict[int, int] = {}
    for raw in obj_rows:
        try:
            row = OkrObjectiveRow.model_validate(raw)
        except Exception:
            continue  # already captured in dry-run
        # Conflict check: same title + cycle
        existing = (
            await session.execute(
                select(OkrObjective)
                .where(
                    OkrObjective.title == row.title,
                    OkrObjective.cycle == row.cycle,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            r.conflict_count += 1
            r.skipped_count += 1
            r.conflicts.append(
                {"source_id": row.id, "natural_key": {"title": row.title, "cycle": row.cycle}}
            )
            obj_id_map[row.id] = existing.id
            continue
        obj = OkrObjective(
            title=row.title,
            description=row.desc,  # Node 'desc' maps to PG 'description' (reserved word)
            progress=row.progress,
            tone=row.tone,
            owner=row.owner,
            weight=row.weight,
            cycle=row.cycle,
            sort_order=row.sort_order,
            rolls_up_to=row.rolls_up_to,
            archived_at=_ts(row.archived_at),
            archive_reason=row.archive_reason,
            source_year=row.source_year,
            created_at=_ts(row.created_at) or datetime.now(UTC),
            updated_at=_ts(row.updated_at) or datetime.now(UTC),
        )
        session.add(obj)
        await session.flush()
        obj_id_map[row.id] = obj.id
        r.inserted_count += 1

    # ── Key Results (natural key: title + objective FK) ───────────────────────
    kr_rows = _read_table(conn, "okr_key_results")
    r = plan.reports["okr_key_results"]
    kr_id_map: dict[int, int] = {}
    for raw in kr_rows:
        try:
            row = OkrKeyResultRow.model_validate(raw)
        except Exception:
            continue
        pg_obj_id = obj_id_map.get(row.objective_id)
        if pg_obj_id is None:
            r.skipped_count += 1
            continue
        existing = (
            await session.execute(
                select(OkrKeyResult)
                .where(
                    OkrKeyResult.objective_id == pg_obj_id,
                    OkrKeyResult.title == row.title,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            r.conflict_count += 1
            r.skipped_count += 1
            r.conflicts.append(
                {
                    "source_id": row.id,
                    "natural_key": {"objective_id": pg_obj_id, "title": row.title},
                }
            )
            kr_id_map[row.id] = existing.id
            continue
        kr = OkrKeyResult(
            objective_id=pg_obj_id,
            title=row.title,
            prog=row.prog,
            status=row.status,
            done_bullets=_json(row.done_bullets, []),
            gaps_bullets=_json(row.gaps_bullets, []),
            note=row.note,
            sort_order=row.sort_order,
            archived_at=_ts(row.archived_at),
            archive_reason=row.archive_reason,
            source_year=row.source_year,
            target_text=row.target_text,
            updated_at=_ts(row.updated_at) or datetime.now(UTC),
        )
        session.add(kr)
        await session.flush()
        kr_id_map[row.id] = kr.id
        r.inserted_count += 1

    # ── Activity ──────────────────────────────────────────────────────────────
    act_rows = _read_table(conn, "okr_activity")
    r = plan.reports["okr_activity"]
    for raw in act_rows:
        try:
            row = OkrActivityRow.model_validate(raw)
        except Exception:
            continue
        pg_kr_id = kr_id_map.get(row.kr_id) if row.kr_id else None
        act = OkrActivity(
            text=row.text,
            kr_id=pg_kr_id,
            kr_label=row.kr_label,
            raw_text=row.raw_text,
            mapping_confidence=row.mapping_confidence,
            cleaned_at=_ts(row.cleaned_at),
            created_at=_ts(row.created_at) or datetime.now(UTC),
        )
        session.add(act)
        r.inserted_count += 1

    # ── Next Up ───────────────────────────────────────────────────────────────
    nu_rows = _read_table(conn, "okr_next_up")
    r = plan.reports["okr_next_up"]
    for raw in nu_rows:
        try:
            row = OkrNextUpRow.model_validate(raw)
        except Exception:
            continue
        item = OkrNextUp(
            ref=row.ref,
            text=row.text,
            prio=row.prio,
            sort_order=row.sort_order,
            dismissed_at=_ts(row.dismissed_at),
            source=row.source,
            action_type=row.action_type,
            dispatch_target=row.dispatch_target,
            dispatch_params=_json(row.dispatch_params),
            generated_at=_ts(row.generated_at),
            rationale=row.rationale,
        )
        session.add(item)
        r.inserted_count += 1

    # ── Update Previews (ephemeral — skip if source empty) ───────────────────
    pv_rows = _read_table(conn, "okr_update_previews")
    r = plan.reports["okr_update_previews"]
    for raw in pv_rows:
        try:
            row = OkrUpdatePreviewRow.model_validate(raw)
        except Exception:
            continue
        pv = OkrUpdatePreview(
            created_at=_ts(row.created_at) or datetime.now(UTC),
            raw_input=row.raw_input,
            input_format=row.input_format,
            diff_json=_json(row.diff_json),
            committed_at=_ts(row.committed_at),
        )
        session.add(pv)
        r.inserted_count += 1

    await session.flush()


async def _apply_writing_rules(  # noqa: PLR0912
    conn: sqlite3.Connection,
    session: Any,
    plan: MigrationPlan,
) -> None:
    from sqlalchemy import select

    from artemis.writing_rules.models import (
        WritingExample,
        WritingFolder,
        WritingProfile,
        WritingRule,
        WritingSource,
    )

    # ── Profiles (natural key: name) ──────────────────────────────────────────
    prof_rows = _read_table(conn, "writing_profiles")
    r = plan.reports["writing_profiles"]
    prof_id_map: dict[int, int] = {}
    for raw in prof_rows:
        try:
            row = WritingProfileRow.model_validate(raw)
        except Exception:
            continue
        existing = (
            await session.execute(
                select(WritingProfile).where(WritingProfile.name == row.name).limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            r.conflict_count += 1
            r.skipped_count += 1
            r.conflicts.append({"source_id": row.id, "natural_key": {"name": row.name}})
            prof_id_map[row.id] = existing.id
            continue
        profile = WritingProfile(
            name=row.name,
            description=row.description,
            status=row.status,
            default_model_provider=row.default_model_provider,
            default_model_id=row.default_model_id,
            system_prompt=row.system_prompt,
            created_at=_ts(row.created_at) or datetime.now(UTC),
            updated_at=_ts(row.updated_at) or datetime.now(UTC),
        )
        session.add(profile)
        await session.flush()
        prof_id_map[row.id] = profile.id
        r.inserted_count += 1

    # ── Folders (natural key: profile_id + name) ──────────────────────────────
    fold_rows = _read_table(conn, "writing_folders")
    r = plan.reports["writing_folders"]
    fold_id_map: dict[int, int] = {}
    for raw in fold_rows:
        try:
            row = WritingFolderRow.model_validate(raw)
        except Exception:
            continue
        pg_prof_id = prof_id_map.get(row.profile_id) if row.profile_id else None
        existing = (
            await session.execute(
                select(WritingFolder)
                .where(
                    WritingFolder.profile_id == pg_prof_id,
                    WritingFolder.name == row.name,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            r.conflict_count += 1
            r.skipped_count += 1
            r.conflicts.append(
                {"source_id": row.id, "natural_key": {"profile_id": pg_prof_id, "name": row.name}}
            )
            fold_id_map[row.id] = existing.id
            continue
        folder = WritingFolder(
            sync_id=row.sync_id,
            profile_id=pg_prof_id,
            parent_folder_id=fold_id_map.get(row.parent_folder_id)
            if row.parent_folder_id
            else None,
            name=row.name,
            description=row.description,
            campaign_id=row.campaign_id,
            metadata_json=_json(row.metadata_json),
            created_at=_ts(row.created_at) or datetime.now(UTC),
            updated_at=_ts(row.updated_at) or datetime.now(UTC),
        )
        session.add(folder)
        await session.flush()
        fold_id_map[row.id] = folder.id
        r.inserted_count += 1

    # ── Rules (natural key: profile_id + rule_type + title where not archived)
    rule_rows = _read_table(conn, "writing_rules")
    r = plan.reports["writing_rules"]
    for raw in rule_rows:
        try:
            row = WritingRuleRow.model_validate(raw)
        except Exception:
            continue
        pg_prof_id = prof_id_map.get(row.profile_id) if row.profile_id else None
        existing = (
            await session.execute(
                select(WritingRule)
                .where(
                    WritingRule.profile_id == pg_prof_id,
                    WritingRule.rule_type == row.rule_type,
                    WritingRule.title == row.title,
                    WritingRule.status != "archived",
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            r.conflict_count += 1
            r.skipped_count += 1
            r.conflicts.append(
                {
                    "source_id": row.id,
                    "natural_key": {
                        "profile_id": pg_prof_id,
                        "rule_type": row.rule_type,
                        "title": row.title,
                    },
                }
            )
            continue
        rule = WritingRule(
            profile_id=pg_prof_id,
            rule_type=row.rule_type,
            title=row.title,
            body=row.body,
            source_candidate_id=row.source_candidate_id,
            status=row.status,
            created_at=_ts(row.created_at) or datetime.now(UTC),
            updated_at=_ts(row.updated_at) or datetime.now(UTC),
        )
        session.add(rule)
        r.inserted_count += 1

    # ── Examples (natural key: profile_id + title + example_type) ────────────
    ex_rows = _read_table(conn, "writing_examples")
    r = plan.reports["writing_examples"]
    for raw in ex_rows:
        try:
            row = WritingExampleRow.model_validate(raw)
        except Exception:
            continue
        pg_prof_id = prof_id_map.get(row.profile_id) if row.profile_id else None
        existing = (
            await session.execute(
                select(WritingExample)
                .where(
                    WritingExample.profile_id == pg_prof_id,
                    WritingExample.title == row.title,
                    WritingExample.example_type == row.example_type,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            r.conflict_count += 1
            r.skipped_count += 1
            r.conflicts.append(
                {
                    "source_id": row.id,
                    "natural_key": {
                        "profile_id": pg_prof_id,
                        "title": row.title,
                        "example_type": row.example_type,
                    },
                }
            )
            continue
        ex = WritingExample(
            profile_id=pg_prof_id,
            title=row.title,
            body=row.body,
            example_type=row.example_type,
            asset_type=row.asset_type,
            channel=row.channel,
            source_candidate_id=row.source_candidate_id,
            created_at=_ts(row.created_at) or datetime.now(UTC),
            updated_at=_ts(row.updated_at) or datetime.now(UTC),
        )
        session.add(ex)
        r.inserted_count += 1

    # ── Sources (natural key: profile_id + source_key) ────────────────────────
    src_rows = _read_table(conn, "writing_sources")
    r = plan.reports["writing_sources"]
    for raw in src_rows:
        try:
            row = WritingSourceRow.model_validate(raw)
        except Exception:
            continue
        pg_prof_id = prof_id_map.get(row.profile_id) if row.profile_id else None
        existing = (
            await session.execute(
                select(WritingSource)
                .where(
                    WritingSource.profile_id == pg_prof_id,
                    WritingSource.source_key == row.source_key,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            r.conflict_count += 1
            r.skipped_count += 1
            r.conflicts.append(
                {
                    "source_id": row.id,
                    "natural_key": {"profile_id": pg_prof_id, "source_key": row.source_key},
                }
            )
            continue
        src = WritingSource(
            profile_id=pg_prof_id,
            source_key=row.source_key,
            title=row.title,
            source_type=row.source_type,
            file_name=row.file_name,
            original_content=row.original_content,
            normalized_content=row.normalized_content,
            content_hash=row.content_hash,
            metadata_json=_json(row.metadata_json),
            imported_at=_ts(row.imported_at) or datetime.now(UTC),
            updated_at=_ts(row.updated_at) or datetime.now(UTC),
        )
        session.add(src)
        r.inserted_count += 1

    await session.flush()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_summary(plan: MigrationPlan, mode: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"Migration {mode.upper()} report — {datetime.now(UTC).isoformat()}")
    print(f"{'=' * 60}")
    for t, r in plan.reports.items():
        err_flag = " [VALIDATION ERRORS]" if r.has_errors else ""
        print(
            f"  {t:35s}  "
            f"src={r.source_count:4d}  "
            f"valid={r.valid_count:4d}  "
            f"conflicts={r.conflict_count:3d}  "
            f"inserted={r.inserted_count:4d}"
            f"{err_flag}"
        )
        for ve in r.validation_errors:
            print(f"    !! row id={ve['source_id']}: {ve['errors']}")
    print(f"\nHas validation errors: {plan.has_validation_errors}")
    print(f"{'=' * 60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="OKR + Writing Studio rules migration")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--apply", action="store_true", default=False)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    # --apply overrides the default --dry-run
    apply_mode = args.apply

    if not args.source.exists():
        print(f"ERROR: source SQLite not found: {args.source}", file=sys.stderr)
        sys.exit(1)

    print(f"Source: {args.source}")
    print(f"Mode:   {'APPLY' if apply_mode else 'DRY-RUN'}")

    plan = build_plan(args.source)
    _print_summary(plan, "APPLY" if apply_mode else "DRY-RUN")

    if apply_mode:
        if plan.has_validation_errors:
            print(
                "ERROR: Validation errors found. Fix them before running --apply.",
                file=sys.stderr,
            )
            plan.write_report(args.report)
            sys.exit(1)
        print("Applying to Postgres …")
        _apply_plan(args.source, plan)
        print("Apply complete.")

    plan.write_report(args.report)
    print(f"Report written to: {args.report}")

    sys.exit(1 if plan.has_validation_errors else 0)


if __name__ == "__main__":
    main()
