"""CCA15 -- tests for artemis/crisis_content/rule_mining.py.

Covers every item in briefs/cca15-mine-suggestions-into-rules.md "Tests"
section: threshold-reached proposal, below-threshold silence with count
persisted, cross-run accumulation, the whole-paragraph-deletion non-pair,
the apostrophe/whitespace typography filters, display-vs-aggregation
casing, test-tab exclusion, idempotent re-running, the "writing_rules is
never written" guarantee, the "existing candidates/examples untouched"
guarantee, and the textRun-vs-element marker-placement regression.

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

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
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
