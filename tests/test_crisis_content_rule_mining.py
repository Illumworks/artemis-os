"""CCA15/CCA16 -- tests for artemis/crisis_content/rule_mining.py.

Covers every item in briefs/cca15-mine-suggestions-into-rules.md "Tests"
section: threshold-reached proposal, below-threshold silence with count
persisted, cross-run accumulation, the whole-paragraph-deletion non-pair,
the apostrophe/whitespace typography filters, display-vs-aggregation
casing, test-tab exclusion, idempotent re-running, the "writing_rules is
never written" guarantee, the "existing candidates/examples untouched"
guarantee, and the textRun-vs-element marker-placement regression.

Also covers every item in briefs/cca16-mine-spans-not-fragments.md "Tests"
section: the live interleaved-rewrite case coalescing to one span pair (not
four run-level fragments), the single-word substitution unaffected by
coalescing (see the CCA15 tests above -- unchanged), an untouched run still
splitting clusters, a deletion-only cluster (including the live ``how it's
made.`` -> "" artifact) and an insertion-only cluster both yielding nothing,
the length guard withholding proposal while still counting, and migration
0114's removal of the six run-level rows leaving everything else untouched.

Fixtures are hand-built Python dicts mirroring the real
``documents.get?includeTabsContent=true&suggestionsViewMode=SUGGESTIONS_INLINE``
JSON shape verified live against the target doc (see the module docstring
in ``artemis/crisis_content/rule_mining.py``) -- this file never calls the
live Docs API or imports ``httpx``.

Engine strategy mirrors ``tests/test_crisis_content_transitions.py``: a
module-level NullPool engine bound to ``ARTEMIS_TEST_DB_URL`` (falling back
to ``artemis_test``), with a hard refusal to run against anything that does
not look like a test database.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import NullPool, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db
import artemis.marketing.models  # noqa: F401 -- registers campaign_deliverables (draft_id FK dep)
from artemis.crisis_content.rule_mining import (
    extract_suggestion_pairs,
    is_noise_pair,
    normalize_for_aggregation,
    record_and_propose,
)
from artemis.crisis_content.rule_mining_orm import (
    CrisisContentRuleMiningObservation,
    CrisisContentRuleMiningPair,
)
from artemis.db import attach_pgvector_codec
from artemis.writing_rules.models import WritingExample, WritingRule, WritingTrainingCandidate

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION_0114 = _ROOT / "alembic/versions/0114_crisis_content_rule_mining_span_reset.py"

# NOTE: no module-level `pytestmark = pytest.mark.asyncio` -- this file mixes
# pure sync tests (extraction/normalization) with DB-backed async tests, and
# `asyncio_mode = "auto"` (pyproject.toml) already detects coroutine test
# functions on its own. Forcing the marker onto sync functions produces a
# PytestWarning per sync test.

_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_test",
)
if "artemis_test" not in _DB_URL:
    raise RuntimeError(
        f"REFUSING TO LOAD test_crisis_content_rule_mining: db_url={_DB_URL!r} "
        "is not a test database. TRUNCATE on the live DB would destroy production data."
    )

_test_engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = async_sessionmaker(
    _test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=True,
    autocommit=False,
)

_TABLES = (
    "crisis_content_rule_mining_observations",
    "crisis_content_rule_mining_pairs",
    "writing_training_candidates",
    "writing_examples",
    "writing_rules",
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session against the shared module-level test engine."""
    async with AsyncSession(_test_engine, expire_on_commit=False) as session:
        await session.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
        await session.commit()
        yield session
        await session.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
        await session.commit()


# ── Fixture builders: real documents.get JSON shape ──────────────────────────


def _run(
    content: str, *, del_ids: list[str] | None = None, add_ids: list[str] | None = None
) -> dict[str, Any]:
    text_run: dict[str, Any] = {"content": content}
    if del_ids:
        text_run["suggestedDeletionIds"] = list(del_ids)
    if add_ids:
        text_run["suggestedInsertionIds"] = list(add_ids)
    return {"startIndex": 0, "endIndex": len(content), "textRun": text_run}


def _para(*elements: dict[str, Any]) -> dict[str, Any]:
    return {"paragraph": {"elements": list(elements)}}


def _cell(*paragraphs: dict[str, Any]) -> dict[str, Any]:
    return {"content": list(paragraphs)}


def _row(*cells: dict[str, Any]) -> dict[str, Any]:
    return {"tableCells": list(cells)}


def _card_table(*, header: str, body_paragraphs: list[dict[str, Any]]) -> dict[str, Any]:
    """One review-card table: row0/cell0 = header, row1 = Platform + Copy review cells.

    Matches the card signature ``_is_review_card_table`` checks for
    ("Platform:" and "Copy review" both present in the table's text) so
    every fixture below is treated as a real review card, not skipped.
    """
    return {
        "table": {
            "tableRows": [
                _row(_cell(_para(_run(header)))),
                _row(
                    _cell(_para(_run("Platform: LinkedIn"))),
                    _cell(_para(_run("Copy review")), *body_paragraphs),
                ),
            ]
        }
    }


def _non_card_table() -> dict[str, Any]:
    """A table that does NOT match the review-card signature (e.g. Strategy Plan)."""
    return {"table": {"tableRows": [_row(_cell(_para(_run("Q3 strategy notes"))))]}}


def _tab(*, tab_id: str, title: str, tables: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tabProperties": {"tabId": tab_id, "title": title},
        "documentTab": {"body": {"content": tables}},
    }


def _document(*tabs: dict[str, Any]) -> dict[str, Any]:
    return {"tabs": list(tabs)}


def _replace_paragraph(
    deleted: str,
    inserted: str,
    suggestion_id: str,
    *,
    prefix: str = "We want every ",
    suffix: str = " to succeed.",
) -> dict[str, Any]:
    """One adjacent DEL->ADD replacement, sandwiched in untouched context text."""
    return _para(
        _run(prefix),
        _run(deleted, del_ids=[suggestion_id]),
        _run(inserted, add_ids=[suggestion_id]),
        _run(suffix),
    )


def _cluster_paragraph(
    *fragments: tuple[str, str],
    prefix: str = "We want every child to know ",
    suffix: str = " and enjoy learning.",
    suggestion_id: str = "s-rewrite",
) -> dict[str, Any]:
    """One paragraph with an interleaved DEL/ADD/DEL/ADD... cluster (CCA16).

    ``fragments`` is a sequence of ``(kind, text)`` pairs -- kind ``"del"``
    or ``"add"`` -- emitted back-to-back with no untouched run between them
    and all sharing ``suggestion_id``, matching the shape a single sentence
    rewritten in Suggesting mode actually takes in the live doc: one edit,
    sliced by Google's diff into several alternating runs. See
    briefs/cca16-mine-spans-not-fragments.md's finding table.
    """
    runs = [_run(prefix)]
    for kind, fragment_text in fragments:
        if kind == "del":
            runs.append(_run(fragment_text, del_ids=[suggestion_id]))
        elif kind == "add":
            runs.append(_run(fragment_text, add_ids=[suggestion_id]))
        else:
            raise ValueError(f"unknown fragment kind {kind!r}")
    runs.append(_run(suffix))
    return _para(*runs)


# ── Pure extraction / normalization tests ────────────────────────────────────


def test_suggestion_markers_read_from_text_run_not_element() -> None:
    """A fixture with markers only on the paragraph element yields nothing.

    This is the exact mistake the CCA15 brief calls out: an earlier probe
    checked ``element["suggestedInsertionIds"]`` (one level too high) and
    found zero suggestions on a doc that had 144. Markers belong on
    ``element["textRun"]``.
    """
    misplaced_element = {
        "startIndex": 10,
        "endIndex": 17,
        # WRONG level -- real markers live inside textRun, not here.
        "suggestedInsertionIds": ["s-wrong-level"],
        "textRun": {"content": "student"},
    }
    paragraph = _para(
        _run("We want every "),
        _run("child", del_ids=["s-wrong-level"]),
        misplaced_element,
        _run(" to succeed."),
    )
    doc = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[_card_table(header="Post A", body_paragraphs=[paragraph])],
        )
    )
    pairs = extract_suggestion_pairs(doc)
    assert pairs == []


def test_whole_paragraph_deletion_with_no_adjacent_insertion_yields_no_pair() -> None:
    """A cut (whole-paragraph deletion, nothing inserted next to it) is not a replacement."""
    paragraph = _para(
        _run("Intro text stays. "),
        _run("This whole sentence is simply removed.", del_ids=["s-cut"]),
    )
    doc = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[_card_table(header="Post A", body_paragraphs=[paragraph])],
        )
    )
    pairs = extract_suggestion_pairs(doc)
    assert pairs == []


def test_live_interleaved_rewrite_yields_one_span_pair_not_four() -> None:
    """CCA16's reproduction of the actual first live pass's fragmentation bug.

    Four run-level DEL/ADD fragments from one sentence Angela rewrote in
    place -- ``the``/``Amira ``, ``It's a``/``students``, ``can't``/`` or ``,
    ``topic``/``. `` -- are contiguous (no untouched run between any of
    them), so they must coalesce into exactly ONE span-level pair, and none
    of the four run-level fragments may appear as a pair on its own. See
    briefs/cca16-mine-spans-not-fragments.md's "The finding" table.
    """
    paragraph = _cluster_paragraph(
        ("del", "the"),
        ("add", "Amira "),
        ("del", "It's a"),
        ("add", "students"),
        ("del", "can't"),
        ("add", " or "),
        ("del", "topic"),
        ("add", ". "),
    )
    doc = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[_card_table(header="Post A", body_paragraphs=[paragraph])],
        )
    )
    pairs = extract_suggestion_pairs(doc)

    assert len(pairs) == 1
    pair = pairs[0]
    assert pair.deleted_text == "theIt's acan'ttopic"
    assert pair.inserted_text == "Amira students or . "
    assert pair.deletion_ids == ("s-rewrite",)
    assert pair.insertion_ids == ("s-rewrite",)

    fragment_pairs = {
        ("the", "Amira "),
        ("It's a", "students"),
        ("can't", " or "),
        ("topic", ". "),
    }
    for other_pair in pairs:
        assert (other_pair.deleted_text, other_pair.inserted_text) not in fragment_pairs


def test_untouched_run_between_two_suggestion_clusters_splits_them() -> None:
    """An untouched run between two DEL/ADD clusters must still end the first
    cluster -- two independent replacements in one paragraph stay two pairs,
    not one pair coalesced across the gap between them.
    """
    paragraph = _para(
        _run("We want every "),
        _run("child", del_ids=["s1"]),
        _run("student", add_ids=["s1"]),
        _run(" to succeed, because "),
        _run("kids", del_ids=["s2"]),
        _run("students", add_ids=["s2"]),
        _run(" deserve support."),
    )
    doc = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[_card_table(header="Post A", body_paragraphs=[paragraph])],
        )
    )
    pairs = extract_suggestion_pairs(doc)

    assert len(pairs) == 2
    assert {(p.deleted_text, p.inserted_text) for p in pairs} == {
        ("child", "student"),
        ("kids", "students"),
    }


def test_deletion_only_cluster_with_empty_adjacent_insertion_yields_no_pair() -> None:
    """The live ``how it's made.`` -> "" artifact.

    A DEL run immediately followed by an ADD run whose content is the
    empty string (a Docs insertion-point boundary marker, not a real
    replacement) must not be recorded as a pair -- the ``not deleted_text
    or not inserted_text`` guard in ``_paragraph_pairs`` is a truthiness
    check precisely so this counts as deletion-only. See
    briefs/cca16-mine-spans-not-fragments.md's finding table and "Also
    required" test list.
    """
    paragraph = _para(
        _run("Read about "),
        _run("how it's made.", del_ids=["s-cut"]),
        _run("", add_ids=["s-cut"]),
    )
    doc = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[_card_table(header="Post A", body_paragraphs=[paragraph])],
        )
    )
    pairs = extract_suggestion_pairs(doc)
    assert pairs == []


def test_insertion_only_cluster_yields_no_pair() -> None:
    """A cluster of ADD blocks with no adjacent DEL block is a pure insertion
    (nothing removed), the mirror image of the whole-paragraph-deletion
    rule -- it must not be recorded as a pair either.
    """
    paragraph = _para(
        _run("We should also mention "),
        _run("the new grant program.", add_ids=["s-insert-only"]),
    )
    doc = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[_card_table(header="Post A", body_paragraphs=[paragraph])],
        )
    )
    pairs = extract_suggestion_pairs(doc)
    assert pairs == []


def test_non_card_tables_are_ignored() -> None:
    """A table without the Platform:/Copy review signature contributes nothing."""
    paragraph = _replace_paragraph("child", "student", "s-not-a-card")
    doc = _document(
        _tab(
            tab_id="t1",
            title="Strategy Plan",
            tables=[
                {
                    "table": {
                        "tableRows": [_row(_cell(paragraph))],
                    }
                }
            ],
        )
    )
    pairs = extract_suggestion_pairs(doc)
    assert pairs == []


def test_is_noise_pair_apostrophe_and_whitespace() -> None:
    assert is_noise_pair("team's", "team’s") is True  # straight -> smart apostrophe
    assert is_noise_pair("a  team", "a team") is True  # whitespace-only
    assert is_noise_pair("child", "student") is False


def test_normalize_for_aggregation_casefolds_and_collapses_whitespace() -> None:
    assert normalize_for_aggregation("Child") == normalize_for_aggregation("child")
    assert normalize_for_aggregation("a   team") == normalize_for_aggregation("a team")


# ── DB-backed mining tests ────────────────────────────────────────────────────


async def _pair_row(db_session: AsyncSession) -> CrisisContentRuleMiningPair | None:
    result = await db_session.execute(
        select(CrisisContentRuleMiningPair).where(
            CrisisContentRuleMiningPair.normalized_deleted == "child",
            CrisisContentRuleMiningPair.normalized_inserted == "student",
        )
    )
    return result.scalar_one_or_none()


async def test_three_child_to_student_pairs_at_threshold_proposes_one_candidate(
    db_session: AsyncSession,
) -> None:
    doc = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[
                _card_table(
                    header="August XX, 2026 - Post A",
                    body_paragraphs=[_replace_paragraph("child", "student", "s1")],
                ),
                _card_table(
                    header="August XX, 2026 - Post B",
                    body_paragraphs=[_replace_paragraph("child", "student", "s2")],
                ),
                _card_table(
                    header="August XX, 2026 - Post C",
                    body_paragraphs=[_replace_paragraph("child", "student", "s3")],
                ),
            ],
        )
    )
    pairs = extract_suggestion_pairs(doc)
    assert len(pairs) == 3

    result = await record_and_propose(db_session, pairs, threshold=3)

    assert result.new_observations == 3
    assert result.skipped_test_tab == 0
    assert result.skipped_noise == 0
    assert len(result.proposed_candidates) == 1

    candidate = result.proposed_candidates[0]
    assert candidate.candidate_type == "rule"
    assert candidate.status == "proposed"
    assert candidate.proposed_text == 'Prefer "student" over "child".'
    assert "3" in (candidate.rationale or "")
    assert "Post A" in (candidate.rationale or "")

    row = await _pair_row(db_session)
    assert row is not None
    assert row.occurrence_count == 3
    assert row.status == "proposed"
    assert row.proposed_candidate_id == candidate.id


async def test_two_occurrences_below_threshold_no_candidate_count_persisted(
    db_session: AsyncSession,
) -> None:
    doc = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[
                _card_table(
                    header="Post A",
                    body_paragraphs=[_replace_paragraph("child", "student", "s1")],
                ),
                _card_table(
                    header="Post B",
                    body_paragraphs=[_replace_paragraph("child", "student", "s2")],
                ),
            ],
        )
    )
    pairs = extract_suggestion_pairs(doc)
    assert len(pairs) == 2

    result = await record_and_propose(db_session, pairs, threshold=3)

    assert result.new_observations == 2
    assert result.proposed_candidates == ()

    row = await _pair_row(db_session)
    assert row is not None
    assert row.occurrence_count == 2
    assert row.status == "counting"

    candidate_count = await db_session.execute(
        select(func.count()).select_from(WritingTrainingCandidate)
    )
    assert candidate_count.scalar_one() == 0


async def test_counts_accumulate_across_two_runs_two_then_one_proposes_on_second(
    db_session: AsyncSession,
) -> None:
    doc_run1 = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[
                _card_table(
                    header="Post A",
                    body_paragraphs=[_replace_paragraph("child", "student", "s1")],
                ),
                _card_table(
                    header="Post B",
                    body_paragraphs=[_replace_paragraph("child", "student", "s2")],
                ),
            ],
        )
    )
    result1 = await record_and_propose(db_session, extract_suggestion_pairs(doc_run1), threshold=3)
    assert result1.new_observations == 2
    assert result1.proposed_candidates == ()

    row_after_run1 = await _pair_row(db_session)
    assert row_after_run1 is not None
    assert row_after_run1.occurrence_count == 2

    # Second poll, days later: one MORE distinct suggestion for the same pair.
    doc_run2 = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[
                _card_table(
                    header="Post C",
                    body_paragraphs=[_replace_paragraph("child", "student", "s3")],
                ),
            ],
        )
    )
    result2 = await record_and_propose(db_session, extract_suggestion_pairs(doc_run2), threshold=3)
    assert result2.new_observations == 1
    assert len(result2.proposed_candidates) == 1

    row_after_run2 = await _pair_row(db_session)
    assert row_after_run2 is not None
    assert row_after_run2.occurrence_count == 3
    assert row_after_run2.status == "proposed"


async def test_apostrophe_only_difference_filtered_no_candidate(db_session: AsyncSession) -> None:
    """The "'s -> 's'" straight/smart-apostrophe pair is Docs autocorrect, not a rule."""
    doc = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[
                _card_table(
                    header="Post A",
                    body_paragraphs=[_replace_paragraph("team's", "team’s", "sq1")],
                )
            ],
        )
    )
    pairs = extract_suggestion_pairs(doc)
    assert len(pairs) == 1  # extraction itself does not filter -- record_and_propose does

    result = await record_and_propose(db_session, pairs, threshold=1)

    assert result.new_observations == 0
    assert result.skipped_noise == 1
    assert result.proposed_candidates == ()

    pairs_in_db = await db_session.execute(
        select(func.count()).select_from(CrisisContentRuleMiningPair)
    )
    assert pairs_in_db.scalar_one() == 0
    obs_in_db = await db_session.execute(
        select(func.count()).select_from(CrisisContentRuleMiningObservation)
    )
    assert obs_in_db.scalar_one() == 0


async def test_whitespace_only_difference_filtered_no_candidate(db_session: AsyncSession) -> None:
    doc = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[
                _card_table(
                    header="Post A",
                    body_paragraphs=[_replace_paragraph("a  team", "a team", "sw1")],
                )
            ],
        )
    )
    pairs = extract_suggestion_pairs(doc)
    result = await record_and_propose(db_session, pairs, threshold=1)

    assert result.new_observations == 0
    assert result.skipped_noise == 1
    assert result.proposed_candidates == ()


async def test_case_preserved_for_display_but_not_for_aggregation(db_session: AsyncSession) -> None:
    """ "Child"/"child" aggregate into one counter; the candidate shows the first-seen casing."""
    doc = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[
                _card_table(
                    header="Post A (first-seen casing)",
                    body_paragraphs=[_replace_paragraph("Child", "Student", "sc1")],
                ),
                _card_table(
                    header="Post B",
                    body_paragraphs=[_replace_paragraph("child", "student", "sc2")],
                ),
                _card_table(
                    header="Post C",
                    body_paragraphs=[_replace_paragraph("child", "student", "sc3")],
                ),
            ],
        )
    )
    pairs = extract_suggestion_pairs(doc)
    assert len(pairs) == 3

    result = await record_and_propose(db_session, pairs, threshold=3)

    assert len(result.proposed_candidates) == 1
    candidate = result.proposed_candidates[0]
    # First-seen casing ("Child"/"Student") wins for display, even though the
    # aggregation key that got it to threshold 3 was case-insensitive.
    assert candidate.proposed_text == 'Prefer "Student" over "Child".'

    row = await _pair_row(db_session)
    assert row is not None
    assert row.occurrence_count == 3
    assert row.display_deleted == "Child"
    assert row.display_inserted == "Student"


async def test_test_tab_cards_contribute_nothing(db_session: AsyncSession) -> None:
    doc = _document(
        _tab(
            tab_id="t-testing",
            title="Content To Review (TESTING)",
            tables=[
                _card_table(
                    header="Post A",
                    body_paragraphs=[_replace_paragraph("child", "student", "st1")],
                ),
                _card_table(
                    header="Post B",
                    body_paragraphs=[_replace_paragraph("child", "student", "st2")],
                ),
                _card_table(
                    header="Post C",
                    body_paragraphs=[_replace_paragraph("child", "student", "st3")],
                ),
            ],
        )
    )
    pairs = extract_suggestion_pairs(doc)
    assert len(pairs) == 3
    assert all(p.is_test_tab for p in pairs)

    result = await record_and_propose(db_session, pairs, threshold=3)

    assert result.new_observations == 0
    assert result.skipped_test_tab == 3
    assert result.proposed_candidates == ()

    row = await _pair_row(db_session)
    assert row is None

    candidate_count = await db_session.execute(
        select(func.count()).select_from(WritingTrainingCandidate)
    )
    assert candidate_count.scalar_one() == 0


async def test_rerunning_proposes_no_duplicate_candidate(db_session: AsyncSession) -> None:
    doc = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[
                _card_table(
                    header="Post A",
                    body_paragraphs=[_replace_paragraph("child", "student", "s1")],
                ),
                _card_table(
                    header="Post B",
                    body_paragraphs=[_replace_paragraph("child", "student", "s2")],
                ),
                _card_table(
                    header="Post C",
                    body_paragraphs=[_replace_paragraph("child", "student", "s3")],
                ),
            ],
        )
    )
    pairs = extract_suggestion_pairs(doc)
    first = await record_and_propose(db_session, pairs, threshold=3)
    assert len(first.proposed_candidates) == 1

    # Re-poll: the exact same document (same suggestion ids) fetched again.
    second = await record_and_propose(db_session, pairs, threshold=3)
    assert second.new_observations == 0
    assert second.proposed_candidates == ()

    # A fourth, genuinely NEW occurrence of the same pair, after it was
    # already proposed -- still must not create a second candidate.
    doc_more = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[
                _card_table(
                    header="Post D",
                    body_paragraphs=[_replace_paragraph("child", "student", "s4")],
                )
            ],
        )
    )
    third = await record_and_propose(db_session, extract_suggestion_pairs(doc_more), threshold=3)
    assert third.new_observations == 1
    assert third.proposed_candidates == ()

    row = await _pair_row(db_session)
    assert row is not None
    assert row.occurrence_count == 4
    assert row.status == "proposed"

    candidate_count = await db_session.execute(
        select(func.count())
        .select_from(WritingTrainingCandidate)
        .where(WritingTrainingCandidate.proposed_text == 'Prefer "student" over "child".')
    )
    assert candidate_count.scalar_one() == 1


async def test_writing_rules_row_count_unchanged(db_session: AsyncSession) -> None:
    before = await db_session.execute(select(func.count()).select_from(WritingRule))
    before_count = before.scalar_one()

    doc = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[
                _card_table(
                    header=f"Post {i}",
                    body_paragraphs=[_replace_paragraph("child", "student", f"s{i}")],
                )
                for i in range(3)
            ],
        )
    )
    result = await record_and_propose(db_session, extract_suggestion_pairs(doc), threshold=3)
    assert len(result.proposed_candidates) == 1  # confirm a proposal really happened

    after = await db_session.execute(select(func.count()).select_from(WritingRule))
    assert after.scalar_one() == before_count


async def test_existing_pending_candidates_and_examples_untouched(db_session: AsyncSession) -> None:
    """Seed Angela's existing pending queue + examples; mining must not disturb them."""
    seeded_candidates = [
        WritingTrainingCandidate(
            candidate_type="rule",
            proposed_text=f"Existing pending candidate #{i}",
            status="proposed",
        )
        for i in range(3)
    ]
    seeded_examples = [
        WritingExample(title=f"Existing example #{i}", body="Some approved copy.") for i in range(2)
    ]
    for row in (*seeded_candidates, *seeded_examples):
        db_session.add(row)
    await db_session.commit()

    candidate_ids_before = {c.id for c in seeded_candidates}
    example_ids_before = {e.id for e in seeded_examples}

    doc = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[
                _card_table(
                    header=f"Post {i}",
                    body_paragraphs=[_replace_paragraph("child", "student", f"s{i}")],
                )
                for i in range(3)
            ],
        )
    )
    result = await record_and_propose(db_session, extract_suggestion_pairs(doc), threshold=3)
    assert len(result.proposed_candidates) == 1

    # The 3 seeded candidates are still there, unmodified.
    seeded_rows = await db_session.execute(
        select(WritingTrainingCandidate).where(
            WritingTrainingCandidate.id.in_(candidate_ids_before)
        )
    )
    remaining = list(seeded_rows.scalars())
    assert len(remaining) == 3
    assert {r.proposed_text for r in remaining} == {c.proposed_text for c in seeded_candidates}
    assert all(r.status == "proposed" for r in remaining)

    # Total candidate count is exactly seeded + 1 new mined proposal.
    total = await db_session.execute(select(func.count()).select_from(WritingTrainingCandidate))
    assert total.scalar_one() == len(seeded_candidates) + 1

    # writing_examples completely untouched.
    example_rows = await db_session.execute(
        select(WritingExample).where(WritingExample.id.in_(example_ids_before))
    )
    remaining_examples = list(example_rows.scalars())
    assert len(remaining_examples) == 2
    example_total = await db_session.execute(select(func.count()).select_from(WritingExample))
    assert example_total.scalar_one() == 2


# ── Length guard (CCA16) ───────────────────────────────────────────────────────


async def test_pair_over_length_guard_is_counted_but_never_proposed(
    db_session: AsyncSession,
) -> None:
    """A pair whose longer side exceeds the length guard still reaches the
    proposal threshold and keeps counting, but is never proposed -- CCA16's
    independent safety net for a long span that genuinely recurs, separate
    from coalescing.
    """
    deleted = "This whole thing is boring"  # 5 words -- over a 4-word guard
    inserted = "This whole thing is engaging"  # 5 words
    doc = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[
                _card_table(
                    header=f"Post {i}",
                    body_paragraphs=[_replace_paragraph(deleted, inserted, f"s{i}")],
                )
                for i in range(3)
            ],
        )
    )
    pairs = extract_suggestion_pairs(doc)
    assert len(pairs) == 3

    result = await record_and_propose(db_session, pairs, threshold=3, max_words=4)

    assert result.new_observations == 3
    assert result.proposed_candidates == ()
    assert result.held_by_length_guard == 1

    pair_row_result = await db_session.execute(
        select(CrisisContentRuleMiningPair).where(
            CrisisContentRuleMiningPair.normalized_deleted == normalize_for_aggregation(deleted),
            CrisisContentRuleMiningPair.normalized_inserted
            == normalize_for_aggregation(inserted),
        )
    )
    pair_row = pair_row_result.scalar_one()
    assert pair_row.occurrence_count == 3
    assert pair_row.status == "counting"
    assert pair_row.proposed_candidate_id is None

    candidate_count = await db_session.execute(
        select(func.count()).select_from(WritingTrainingCandidate)
    )
    assert candidate_count.scalar_one() == 0


async def test_pair_within_length_guard_proposes_normally(db_session: AsyncSession) -> None:
    """One word under the same guard: reaching the threshold proposes as usual."""
    deleted = "This thing is boring"  # 4 words -- exactly at a 4-word guard
    inserted = "This thing is engaging"  # 4 words
    doc = _document(
        _tab(
            tab_id="t1",
            title="Content To Review",
            tables=[
                _card_table(
                    header=f"Post {i}",
                    body_paragraphs=[_replace_paragraph(deleted, inserted, f"s{i}")],
                )
                for i in range(3)
            ],
        )
    )
    pairs = extract_suggestion_pairs(doc)
    assert len(pairs) == 3

    result = await record_and_propose(db_session, pairs, threshold=3, max_words=4)

    assert result.held_by_length_guard == 0
    assert len(result.proposed_candidates) == 1
    assert result.proposed_candidates[0].proposed_text == (
        f'Prefer "{inserted}" over "{deleted}".'
    )


# ── Migration 0114 (CCA16) ──────────────────────────────────────────────────────


def _load_migration_0114() -> Any:
    spec = importlib.util.spec_from_file_location("cca16_migration_0114", _MIGRATION_0114)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0114_chains_after_0113() -> None:
    module = _load_migration_0114()
    assert module.revision == "0114"
    assert module.down_revision == "0113"


async def test_migration_0114_clears_rule_mining_rows_leaves_everything_else(
    db_session: AsyncSession,
) -> None:
    """Seed the run-level six-row shape plus unrelated Writing Studio rows;
    running 0114's ``upgrade()`` must clear only the two rule-mining tables.
    """
    module = _load_migration_0114()

    # The six-row shape CCA15's run-level extractor actually produced live
    # (see briefs/cca16-mine-spans-not-fragments.md's finding table) --
    # exact text does not matter to the migration, which deletes
    # unconditionally, but this is the production shape being cleared.
    live_rows = [
        ("the", "Amira "),
        ("It's a", "students"),
        ("how it's made.", ""),
        ("can't", " or "),
        ("topic", ". "),
        (", can't surprise you. Boring, on purpose.", " Predictable, on purpose. "),
    ]
    for i, (deleted, inserted) in enumerate(live_rows):
        db_session.add(
            CrisisContentRuleMiningObservation(
                occurrence_key=f"live-key-{i}",
                normalized_deleted=normalize_for_aggregation(deleted),
                normalized_inserted=normalize_for_aggregation(inserted),
                deleted_text=deleted,
                inserted_text=inserted,
                tab_id="t1",
                tab_title="Content To Review",
                card_header=f"Post {i}",
            )
        )
        db_session.add(
            CrisisContentRuleMiningPair(
                normalized_deleted=normalize_for_aggregation(deleted),
                normalized_inserted=normalize_for_aggregation(inserted),
                display_deleted=deleted,
                display_inserted=inserted,
                occurrence_count=1,
                status="counting",
            )
        )

    # Unrelated Writing Studio state that 0114 must leave completely alone.
    seeded_rule = WritingRule(title="Existing rule", body="Some standing guidance.")
    seeded_example = WritingExample(title="Existing example", body="Some approved copy.")
    seeded_candidate = WritingTrainingCandidate(
        candidate_type="rule",
        proposed_text="Existing pending candidate",
        status="proposed",
    )
    db_session.add_all([seeded_rule, seeded_example, seeded_candidate])
    await db_session.commit()

    obs_before = await db_session.execute(
        select(func.count()).select_from(CrisisContentRuleMiningObservation)
    )
    assert obs_before.scalar_one() == len(live_rows)
    pairs_before = await db_session.execute(
        select(func.count()).select_from(CrisisContentRuleMiningPair)
    )
    assert pairs_before.scalar_one() == len(live_rows)

    def _apply_upgrade(sync_conn: Any) -> None:
        context = MigrationContext.configure(sync_conn)
        with Operations.context(context):
            module.upgrade()

    connection = await db_session.connection()
    await connection.run_sync(_apply_upgrade)
    await db_session.commit()

    obs_after = await db_session.execute(
        select(func.count()).select_from(CrisisContentRuleMiningObservation)
    )
    assert obs_after.scalar_one() == 0
    pairs_after = await db_session.execute(
        select(func.count()).select_from(CrisisContentRuleMiningPair)
    )
    assert pairs_after.scalar_one() == 0

    # writing_rules, writing_examples, and the pending candidate are untouched.
    rule_row = await db_session.get(WritingRule, seeded_rule.id)
    assert rule_row is not None
    assert rule_row.title == "Existing rule"
    example_row = await db_session.get(WritingExample, seeded_example.id)
    assert example_row is not None
    assert example_row.title == "Existing example"
    candidate_row = await db_session.get(WritingTrainingCandidate, seeded_candidate.id)
    assert candidate_row is not None
    assert candidate_row.status == "proposed"

    rule_total = await db_session.execute(select(func.count()).select_from(WritingRule))
    assert rule_total.scalar_one() == 1
    example_total = await db_session.execute(select(func.count()).select_from(WritingExample))
    assert example_total.scalar_one() == 1
    candidate_total = await db_session.execute(
        select(func.count()).select_from(WritingTrainingCandidate)
    )
    assert candidate_total.scalar_one() == 1
