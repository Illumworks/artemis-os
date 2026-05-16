"""Fixtures for Phase H prep tests.

Uses the same test Postgres as the rest of the suite (ARTEMIS_TEST_DB_URL or
settings.db_url). Tables are truncated before each test for isolation.

Also provides a helper that creates a minimal in-memory SQLite fixture with
the Node schema shape for migration script tests.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Register all models so Base.metadata knows about them
import artemis.okr.models  # noqa: F401
import artemis.writing_rules.models  # noqa: F401
from artemis.config import settings
from artemis.db import attach_pgvector_codec

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL", settings.db_url)
_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_engine)

_TRUNCATE_SQL = text(
    "TRUNCATE "
    "writing_sources, writing_examples, writing_rules, writing_folders, writing_profiles, "
    "okr_update_previews, okr_next_up, okr_activity, okr_key_results, okr_objectives "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session — OKR + writing_rules tables truncated before each test."""
    async with AsyncSession(_engine, expire_on_commit=False) as session:
        async with session.begin():
            await session.execute(_TRUNCATE_SQL)
        yield session


def _make_sqlite_fixture(
    *,
    objectives: list[dict] | None = None,
    key_results: list[dict] | None = None,
    activity: list[dict] | None = None,
    next_up: list[dict] | None = None,
    update_previews: list[dict] | None = None,
    profiles: list[dict] | None = None,
    folders: list[dict] | None = None,
    rules: list[dict] | None = None,
    examples: list[dict] | None = None,
    sources: list[dict] | None = None,
) -> Path:
    """Create a temp SQLite file with the Node schema shape and provided rows.

    Returns the path to the file. Caller is responsible for cleanup.
    """
    # NamedTemporaryFile with delete=False gives us a path we can open with sqlite3.
    # SIM115 doesn't apply here: we deliberately open, close, then pass path to sqlite3.
    path = Path(tempfile.mkstemp(suffix=".db")[1])

    conn = sqlite3.connect(str(path))
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE okr_objectives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,  -- nullable to allow corruption tests; real schema has NOT NULL
            desc TEXT,
            progress INTEGER DEFAULT 0,
            tone TEXT DEFAULT 'sage',
            owner TEXT,
            weight TEXT,
            cycle TEXT,
            sort_order INTEGER DEFAULT 0,
            rolls_up_to TEXT,
            archived_at INTEGER,
            archive_reason TEXT,
            source_year INTEGER,
            created_at INTEGER,
            updated_at INTEGER
        );
        CREATE TABLE okr_key_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            objective_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            prog INTEGER DEFAULT 0,
            status TEXT DEFAULT 'notstarted',
            done_bullets TEXT DEFAULT '[]',
            gaps_bullets TEXT DEFAULT '[]',
            note TEXT,
            sort_order INTEGER DEFAULT 0,
            archived_at INTEGER,
            archive_reason TEXT,
            source_year INTEGER,
            target_text TEXT,
            updated_at INTEGER
        );
        CREATE TABLE okr_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            kr_id INTEGER,
            kr_label TEXT,
            raw_text TEXT,
            mapping_confidence REAL,
            cleaned_at INTEGER,
            created_at INTEGER
        );
        CREATE TABLE okr_next_up (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ref TEXT DEFAULT '—',
            text TEXT NOT NULL,
            prio TEXT DEFAULT 'med',
            sort_order INTEGER DEFAULT 0,
            dismissed_at INTEGER,
            source TEXT DEFAULT 'manual',
            action_type TEXT DEFAULT 'advice',
            dispatch_target TEXT,
            dispatch_params TEXT,
            generated_at INTEGER,
            rationale TEXT
        );
        CREATE TABLE okr_update_previews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER,
            raw_input TEXT,
            input_format TEXT DEFAULT 'text',
            diff_json TEXT,
            committed_at INTEGER
        );
        CREATE TABLE writing_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,  -- nullable to allow corruption tests
            description TEXT,
            status TEXT DEFAULT 'active',
            default_model_provider TEXT,
            default_model_id TEXT,
            system_prompt TEXT,
            created_at INTEGER,
            updated_at INTEGER
        );
        CREATE TABLE writing_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_id TEXT,
            profile_id INTEGER,
            parent_folder_id INTEGER,
            name TEXT NOT NULL,
            description TEXT,
            campaign_id TEXT,
            metadata_json TEXT,
            created_at INTEGER,
            updated_at INTEGER
        );
        CREATE TABLE writing_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER,
            rule_type TEXT DEFAULT 'voice',
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            source_candidate_id INTEGER,
            status TEXT DEFAULT 'active',
            created_at INTEGER,
            updated_at INTEGER
        );
        CREATE TABLE writing_examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            example_type TEXT DEFAULT 'reference',
            asset_type TEXT,
            channel TEXT,
            source_candidate_id INTEGER,
            created_at INTEGER,
            updated_at INTEGER
        );
        CREATE TABLE writing_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER,
            source_key TEXT NOT NULL,
            title TEXT NOT NULL,
            source_type TEXT DEFAULT 'reference',
            file_name TEXT,
            original_content TEXT NOT NULL,
            normalized_content TEXT NOT NULL,
            content_hash TEXT,
            metadata_json TEXT,
            imported_at INTEGER,
            updated_at INTEGER
        );
    """)

    def insert_rows(table: str, rows: list[dict] | None) -> None:
        if not rows:
            return
        for row in rows:
            cols = ", ".join(row.keys())
            placeholders = ", ".join("?" for _ in row)
            cur.execute(
                f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",  # noqa: S608
                list(row.values()),
            )

    insert_rows("okr_objectives", objectives)
    insert_rows("okr_key_results", key_results)
    insert_rows("okr_activity", activity)
    insert_rows("okr_next_up", next_up)
    insert_rows("okr_update_previews", update_previews)
    insert_rows("writing_profiles", profiles)
    insert_rows("writing_folders", folders)
    insert_rows("writing_rules", rules)
    insert_rows("writing_examples", examples)
    insert_rows("writing_sources", sources)

    conn.commit()
    conn.close()
    return path


@pytest.fixture
def sqlite_fixture(tmp_path: Path) -> Path:
    """Standard synthetic SQLite fixture with a few rows of each type."""
    return _make_sqlite_fixture(
        objectives=[
            {
                "id": 1,
                "title": "Test Objective Alpha",
                "desc": "First test objective",
                "progress": 50,
                "tone": "sage",
                "owner": "Alice",
                "cycle": "Q1 2026",
                "sort_order": 0,
                "created_at": 1700000000,
                "updated_at": 1700000000,
            },
            {
                "id": 2,
                "title": "Test Objective Beta",
                "desc": None,
                "progress": 0,
                "tone": "warn",
                "cycle": "Q1 2026",
                "sort_order": 1,
                "created_at": 1700001000,
                "updated_at": 1700001000,
            },
        ],
        key_results=[
            {
                "id": 1,
                "objective_id": 1,
                "title": "KR1 for Alpha",
                "prog": 60,
                "status": "atrisk",
                "done_bullets": json.dumps(["Done thing A"]),
                "gaps_bullets": "[]",
                "sort_order": 0,
                "updated_at": 1700000500,
            },
        ],
        activity=[
            {
                "id": 1,
                "text": "Shipped the widget",
                "kr_id": 1,
                "kr_label": "KR1 for Alpha",
                "created_at": 1700002000,
            },
        ],
        next_up=[
            {
                "id": 1,
                "ref": "OBJ-1",
                "text": "Review progress with team",
                "prio": "high",
                "sort_order": 0,
                "source": "manual",
                "action_type": "advice",
            },
        ],
        profiles=[
            {
                "id": 1,
                "name": "Test Writing Profile",
                "description": "A test profile",
                "status": "active",
                "system_prompt": "You are helpful.",
                "created_at": 1700000000,
                "updated_at": 1700000000,
            },
        ],
        folders=[
            {
                "id": 1,
                "profile_id": 1,
                "name": "Test Folder",
                "description": "A test folder",
                "created_at": 1700000000,
                "updated_at": 1700000000,
            },
        ],
        rules=[
            {
                "id": 1,
                "profile_id": 1,
                "rule_type": "voice",
                "title": "Be concise",
                "body": "Keep sentences short.",
                "status": "active",
                "created_at": 1700000000,
                "updated_at": 1700000000,
            },
        ],
        examples=[
            {
                "id": 1,
                "profile_id": 1,
                "title": "Good example headline",
                "body": "This is how we write headlines.",
                "example_type": "reference",
                "created_at": 1700000000,
                "updated_at": 1700000000,
            },
        ],
        sources=[
            {
                "id": 1,
                "profile_id": 1,
                "source_key": "TEST_SOURCE",
                "title": "Test Source Document",
                "source_type": "reference",
                "original_content": "Raw content here.",
                "normalized_content": "Normalized content here.",
                "imported_at": 1700000000,
                "updated_at": 1700000000,
            },
        ],
    )


@pytest.fixture
def empty_sqlite_fixture(tmp_path: Path) -> Path:
    """SQLite fixture with the right schema but zero rows in all tables."""
    return _make_sqlite_fixture()
