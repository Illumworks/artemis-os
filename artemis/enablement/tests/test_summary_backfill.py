"""Writing the missing catalog summaries from the assets themselves.

289 of 416 assets have no summary, so they embed on their title alone and lose
every compound question — the reason Sara's rostering question returned a parent
PDF.

They were empty because `generate_enrichment` correctly refused: 212 of them
carry under 80 characters of record text, and a summary written from a title is
a paraphrase wearing a description's clothes. What changed is that we can now
open the file.

The honesty bar is the thing under test. An asset is summarised ONLY when the
fetch returns real content; below the threshold it stays unsummarised, because
no summary is truthful and a generated one would not be.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from artemis.enablement import summary_backfill as mod
from artemis.enablement.summary_backfill import MIN_CONTENT_CHARS, _is_unfetchable


def test_interactive_and_media_hosts_are_skipped_up_front() -> None:
    """63 walkthroughs and 103 videos sit behind these; fetching returns chrome."""
    for url in (
        "https://app.storylane.io/demo/abc",
        "https://www.youtube.com/watch?v=x",
        "https://youtu.be/x",
        "https://vimeo.com/123",
        "https://www.loom.com/share/x",
    ):
        assert _is_unfetchable(url), url


def test_document_hosts_are_not_skipped() -> None:
    for url in (
        "https://explore.amiralearning.com/hubfs/Guide.pdf",
        "https://drive.google.com/file/d/abc/view",
        "https://docs.google.com/document/d/abc/edit",
        "https://amiralearning.com/teacher-resource-hub",
    ):
        assert not _is_unfetchable(url), url


def test_the_content_bar_is_well_above_the_record_text_it_replaces() -> None:
    """Record text averages under 80 chars for these assets.

    If the threshold sat near that, the backfill would "succeed" on assets whose
    fetch told us nothing — which is the failure it exists to avoid.
    """
    assert MIN_CONTENT_CHARS >= 300


class _Asset(SimpleNamespace):
    pass


def _asset(**kw: object) -> _Asset:
    base = dict(
        id=1,
        drive_file_id="d1",
        title="Amira Technical Guide",
        asset_name="Amira Technical Guide",
        type="teacher_resource",
        tags=[],
        audience=None,
        source_sheet=None,
        transcript_text=None,
        searchable_text="Amira Technical Guide",
        links=[],
        drive_link="https://explore.amiralearning.com/g.pdf",
        summary=None,
        summary_status=None,
        format=None,
        grade_range=None,
        embedding=None,
    )
    base.update(kw)
    return _Asset(**base)


class _Session:
    def __init__(self, assets: list[_Asset]) -> None:
        self._assets = assets
        self.flushed = False

    async def execute(self, _stmt: object) -> object:
        assets = self._assets

        class _R:
            def scalars(self_inner):  # noqa: N805
                class _S:
                    def all(self_s):  # noqa: N805
                        return assets

                return _S()

        return _R()

    async def flush(self) -> None:
        self.flushed = True


@pytest.mark.asyncio
async def test_too_little_fetched_content_leaves_the_asset_unsummarised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE honesty bar. No summary is truthful; a generated one would not be."""
    asset = _asset()

    async def _thin(_url: str) -> str:
        return "Log in page."

    called = False

    async def _generate(*_a: object, **_k: object) -> object:
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(mod, "_fetch_document_text", _thin)
    monkeypatch.setattr("artemis.enablement.enrichment.generate_enrichment", _generate)

    report = await mod.backfill_summaries(_Session([asset]), limit=5)

    assert report.summarised == 0
    assert report.too_little_content == 1
    assert asset.summary is None, "the row must be untouched"
    assert not called, "the model must not even be asked without real content"


@pytest.mark.asyncio
async def test_a_fetch_failure_is_counted_and_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _empty(_url: str) -> str:
        return ""

    monkeypatch.setattr(mod, "_fetch_document_text", _empty)

    report = await mod.backfill_summaries(_Session([_asset()]), limit=5)

    assert report.fetch_failed == 1
    assert report.summarised == 0


@pytest.mark.asyncio
async def test_a_good_fetch_writes_a_draft_and_reembeds(monkeypatch: pytest.MonkeyPatch) -> None:
    asset = _asset()
    reembedded: list[object] = []

    async def _rich(_url: str) -> str:
        return "x" * (MIN_CONTENT_CHARS + 50)

    async def _generate(facts: object, **_k: object) -> object:
        # The fetched text must actually reach the model, or the summary is
        # written from the title after all.
        assert getattr(facts, "document_text", None)
        return SimpleNamespace(
            summary="Technical requirements guide for district IT staff.",
            audience="Admin",
            format="pdf",
            grade_range=None,
        )

    async def _reembed(a: object) -> bool:
        reembedded.append(a)
        return True

    monkeypatch.setattr(mod, "_fetch_document_text", _rich)
    monkeypatch.setattr("artemis.enablement.enrichment.generate_enrichment", _generate)
    monkeypatch.setattr("artemis.enablement.enrichment.reembed", _reembed)

    report = await mod.backfill_summaries(_Session([asset]), limit=5)

    assert report.summarised == 1
    assert asset.summary == "Technical requirements guide for district IT staff."
    assert asset.summary_status == "ai_draft", "never lands as verified"
    assert reembedded, "without a re-embed the summary never reaches semantic search"


@pytest.mark.asyncio
async def test_a_dry_run_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    asset = _asset()

    async def _rich(_url: str) -> str:
        return "x" * (MIN_CONTENT_CHARS + 50)

    async def _generate(*_a: object, **_k: object) -> object:
        return SimpleNamespace(summary="A summary.", audience=None, format=None, grade_range=None)

    monkeypatch.setattr(mod, "_fetch_document_text", _rich)
    monkeypatch.setattr("artemis.enablement.enrichment.generate_enrichment", _generate)

    session = _Session([asset])
    report = await mod.backfill_summaries(session, limit=5, dry_run=True)

    assert report.summarised == 1
    assert asset.summary is None, "dry run must not touch the row"
    assert not session.flushed


@pytest.mark.asyncio
async def test_every_considered_asset_is_accounted_for(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing may vanish silently between considered and the outcome buckets."""
    assets = [
        _asset(id=1, drive_link="https://app.storylane.io/demo/a"),
        _asset(id=2),
        _asset(id=3),
    ]

    async def _mixed(url: str) -> str:
        return "" if "storylane" in url else "short"

    monkeypatch.setattr(mod, "_fetch_document_text", _mixed)

    report = await mod.backfill_summaries(_Session(assets), limit=10)

    counted = (
        report.summarised
        + report.too_little_content
        + report.unfetchable_host
        + report.fetch_failed
        + report.model_declined
    )
    assert counted == report.considered == 3
    assert len(report.skipped_titles) == 3
