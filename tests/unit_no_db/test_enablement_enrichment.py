"""Stream 3 — AI-drafted catalog enrichment and its review gate.

All 416 assets had no summary and 129 no audience, so nearly every Kai answer
carried "Caveat: Needs verification -- the catalog records don't include a
summary". Sara asked for a Google Slides deck and got a PDF (no format field);
"Reading Risk report: K-8 or PK-8?" was unanswerable (no grade metadata).

Owner decision (Jon, 2026-08-11): AI writes summaries, Sara and Missy review,
feedback regenerates. The thing that keeps that from recreating the 2026-08-10
failure is the status field: a generated summary is 'ai_draft' and Kai caveats
it. Only a human review action can produce 'enablement_verified'.

These tests pin the validation and grounding rules. The live review loop
(approve / approve-with-edit / send-back-and-regenerate) was exercised against
the running app and the real catalog.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from artemis.enablement.enrichment import (
    FORMATS,
    GRADE_RANGES,
    STATUS_AI_DRAFT,
    STATUS_NEEDS_REVISION,
    STATUS_VERIFIED,
    AssetEnrichment,
    AssetFacts,
    apply_enrichment,
    build_user_prompt,
)

FACTS = AssetFacts(
    drive_file_id="sheet:row1",
    title="Getting Started with Amira Assess",
    asset_name="Getting Started",
    asset_type="training_deck",
    tags=["Assess", "Teacher"],
    audience=None,
    source_sheet="teacher_resources_internal",
    transcript_text=None,
    searchable_text="Slide 1: welcome. Slide 2: logging in.",
    links=[{"url": "https://docs.google.com/presentation/d/x", "visibility": "internal"}],
)


def _row(**kwargs: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "summary": None,
        "summary_status": None,
        "summary_generated_at": None,
        "summary_reviewed_by": "someone",
        "summary_reviewed_at": "then",
        "summary_feedback": "old note",
        "audience": None,
        "format": None,
        "grade_range": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


# ── Validation: a wrong value is worse than a missing one ─────────────────────


def test_unknown_format_is_dropped_not_stored() -> None:
    """A bogus format silently breaks the filter Sara needs. Null is honest."""
    e = AssetEnrichment(summary="A" * 25, format="powerpoint-ish thing")
    assert e.format is None


def test_unknown_grade_range_is_dropped() -> None:
    assert AssetEnrichment(summary="A" * 25, grade_range="grades 4 and 5").grade_range is None


@pytest.mark.parametrize("value", FORMATS)
def test_every_known_format_survives(value: str) -> None:
    assert AssetEnrichment(summary="A" * 25, format=value).format == value


@pytest.mark.parametrize("value", GRADE_RANGES)
def test_every_known_grade_range_survives(value: str) -> None:
    assert AssetEnrichment(summary="A" * 25, grade_range=value).grade_range == value


@pytest.mark.parametrize("raw", ["Google Slides", "google-slides", "  GOOGLE_SLIDES "])
def test_format_normalization(raw: str) -> None:
    assert AssetEnrichment(summary="A" * 25, format=raw).format == "google_slides"


@pytest.mark.parametrize("raw", ["null", "none", "unknown", "", "  "])
def test_placeholder_values_become_null(raw: str) -> None:
    e = AssetEnrichment(summary="A" * 25, format=raw, grade_range=raw, audience=raw)
    assert (e.format, e.grade_range, e.audience) == (None, None, None)


def test_summary_must_have_substance() -> None:
    """A one-word summary is not worth marking the catalog complete with."""
    with pytest.raises(ValidationError):
        AssetEnrichment(summary="A deck")


def test_summary_length_is_capped() -> None:
    with pytest.raises(ValidationError):
        AssetEnrichment(summary="x" * 401)


def test_summary_whitespace_is_collapsed() -> None:
    assert AssetEnrichment(summary="a  \n  b" + "c" * 20).summary.startswith("a b")


# ── Grounding: the prompt only ever shows the record ──────────────────────────


def test_prompt_carries_the_record_fields() -> None:
    prompt = build_user_prompt(FACTS)
    assert "Getting Started with Amira Assess" in prompt
    assert "training_deck" in prompt
    assert "Assess, Teacher" in prompt
    assert "docs.google.com/presentation" in prompt


def test_prompt_marks_absent_fields_rather_than_hiding_them() -> None:
    bare = AssetFacts("id", None, None, None, [], None, None, None, None, [])
    prompt = build_user_prompt(bare)
    assert "(none)" in prompt
    assert "body text: (none available)" in prompt


def test_reviewer_feedback_is_given_priority_in_the_prompt() -> None:
    prompt = build_user_prompt(FACTS, feedback="This is for admins, not teachers.")
    assert "This is for admins, not teachers." in prompt
    assert "takes priority" in prompt


def test_system_prompt_forbids_the_claims_the_record_cannot_support() -> None:
    from artemis.enablement.enrichment import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    assert "never assert" in lowered
    assert "approved" in lowered
    assert "you cannot open the file" in lowered
    assert "do not \\\n" not in SYSTEM_PROMPT  # no broken continuation
    assert "null" in lowered


def test_long_body_text_is_truncated() -> None:
    facts = AssetFacts("id", "t", None, None, [], None, None, "word " * 2000, None, [])
    assert len(build_user_prompt(facts)) < 3000


# ── apply_enrichment: always a draft, never overwrites a human ────────────────


def test_generated_summary_always_lands_as_draft() -> None:
    """Nothing in the generator may produce a verified record."""
    row = _row()
    apply_enrichment(row, AssetEnrichment(summary="A useful description here."))
    assert row.summary_status == STATUS_AI_DRAFT
    assert row.summary_status != STATUS_VERIFIED


def test_applying_a_draft_clears_stale_review_state() -> None:
    """A fresh draft has not been reviewed, so it must not look like it has."""
    row = _row(summary_status=STATUS_NEEDS_REVISION)
    apply_enrichment(row, AssetEnrichment(summary="A useful description here."))
    assert row.summary_reviewed_by is None
    assert row.summary_reviewed_at is None
    assert row.summary_feedback is None
    assert row.summary_generated_at is not None


def test_existing_audience_is_never_overwritten() -> None:
    """287 rows carry a curated audience. A model guess must not clobber one."""
    row = _row(audience="District Leader")
    apply_enrichment(row, AssetEnrichment(summary="A useful description here.", audience="Teacher"))
    assert row.audience == "District Leader"


def test_blank_audience_is_filled() -> None:
    row = _row(audience="   ")
    apply_enrichment(row, AssetEnrichment(summary="A useful description here.", audience="Teacher"))
    assert row.audience == "Teacher"


def test_facets_fill_only_when_empty() -> None:
    row = _row(format="pdf", grade_range="K-2")
    apply_enrichment(
        row,
        AssetEnrichment(summary="A useful description here.", format="video", grade_range="6-8"),
    )
    assert row.format == "pdf"
    assert row.grade_range == "K-2"


def test_null_facets_leave_the_row_alone() -> None:
    row = _row()
    apply_enrichment(row, AssetEnrichment(summary="A useful description here."))
    assert row.format is None and row.grade_range is None and row.audience is None


# ── Kai's view of a draft ────────────────────────────────────────────────────


def test_tool_output_exposes_the_status_kai_must_caveat_on() -> None:
    from artemis.enablement.tools import _asset_to_dict

    row = SimpleNamespace(
        asset_name="n",
        title="t",
        summary="s",
        summary_status=STATUS_AI_DRAFT,
        format="pdf",
        grade_range=None,
        drive_link=None,
        links=[],
        requires_copy=False,
        type="doc",
        confidence_label=None,
        audience=None,
        tags=[],
        transcript_link=None,
        status="active",
        source_scope="enablement",
        source_sheet=None,
        drive_file_id="x",
    )
    record = _asset_to_dict(row)
    assert record["summary_status"] == STATUS_AI_DRAFT
    assert record["format"] == "pdf"
    assert record["grade_range"] is None


def test_persona_no_longer_parrots_the_ai_draft_caveat() -> None:
    """Owner decision 2026-08-11: nobody has time to review 400+ summaries, so
    Kai stops announcing provenance on every answer. The hedge was noise."""
    from artemis.floating_artemis import personality as pm

    core = pm.load_agent_profile("kai").persona_core
    lowered = core.lower()
    assert "do not announce" in lowered
    assert "needs_revision" in lowered  # the one status that still suppresses text
    # The format/grade guidance that depends on the 0105 fields.
    assert "grade_range" in lowered
    assert "without the filter" in lowered


def test_persona_still_forbids_the_claims_a_summary_cannot_support() -> None:
    """Dropping the caveat must NOT drop the truthfulness rules underneath it."""
    core = (
        __import__("artemis.floating_artemis.personality", fromlist=["x"])
        .load_agent_profile("kai")
        .persona_core.lower()
    )
    assert "approved, current, the latest version, or effective" in core
    assert "is not evidence" in core  # hold-your-ground survives


# ── Re-embed: the reason summaries exist at all ──────────────────────────────
#
# summary is part of _embedding_text at ingest, but the embedding is only
# computed there. Writing a summary straight to the row left the vector stale,
# so generated summaries reached keyword search and nothing else -- i.e. they
# did almost nothing for the AI retrieval they were written for. Found
# 2026-08-11 when Jon asked what the summaries were actually for.


def test_embedding_text_matches_the_ingest_time_recipe() -> None:
    """If these drift, re-embedded rows stop being comparable to ingested ones."""
    import inspect

    from artemis.enablement.enrichment import embedding_text_for
    from artemis.routes.enablement import _embedding_text

    source = inspect.getsource(_embedding_text)
    for field in ("title", "summary", "tags", "audience", "searchable_text"):
        assert field in source, f"ingest recipe changed: {field}"

    row = SimpleNamespace(
        title="T", summary="S", tags=["a", "b"], audience="Teacher", searchable_text="BODY"
    )
    assert embedding_text_for(row) == "T S a b Teacher BODY"


def test_embedding_text_includes_the_summary() -> None:
    """The whole point: the new summary must reach the vector."""
    from artemis.enablement.enrichment import embedding_text_for

    row = SimpleNamespace(
        title="T", summary="a distinctive phrase", tags=[], audience=None, searchable_text=None
    )
    assert "a distinctive phrase" in embedding_text_for(row)


async def test_reembed_writes_a_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    from artemis.enablement import enrichment as enr

    class _Provider:
        async def embed(self, text: str) -> list[float]:
            return [0.5] * 384

    monkeypatch.setattr("artemis.memory.embeddings.MiniLMProvider", _Provider)
    row = SimpleNamespace(
        title="T", summary="S", tags=[], audience=None, searchable_text=None, embedding=None
    )
    assert await enr.reembed(row) is True
    assert row.embedding == [0.5] * 384


async def test_reembed_failure_leaves_the_old_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed re-embed must degrade to the previous behaviour, not corrupt the row."""
    from artemis.enablement import enrichment as enr

    class _Boom:
        async def embed(self, text: str) -> list[float]:
            raise RuntimeError("model unavailable")

    monkeypatch.setattr("artemis.memory.embeddings.MiniLMProvider", _Boom)
    row = SimpleNamespace(
        title="T", summary="S", tags=[], audience=None, searchable_text=None, embedding=["old"]
    )
    assert await enr.reembed(row) is False
    assert row.embedding == ["old"]


async def test_reembed_skips_an_empty_record() -> None:
    from artemis.enablement import enrichment as enr

    row = SimpleNamespace(
        title=None, summary=None, tags=[], audience=None, searchable_text=None, embedding=None
    )
    assert await enr.reembed(row) is False
    assert row.embedding is None
