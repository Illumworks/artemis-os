"""Tests for the Brand Signals brief.

``compose_brand_brief`` is pure, so the wording Angela reads is pinned here.
The counts in the bottom line are the highest-risk part of this module: a brief
that overstates how much coverage names Amira would send a crisis team at a
problem that is not there, and understating it did actually happen once.
"""

from datetime import UTC, datetime

from artemis.screentime.national_news import NATIONAL
from artemis.sentiment.report import (
    LOOKBACK_DAYS,
    compose_brand_brief,
    row_to_dict,
    split_for_slack,
)
from artemis.sentiment.themes import (
    THEME_INSTITUTIONAL_REJECTION,
    THEME_PARENT_OBJECTION,
)

NOW = datetime(2026, 8, 25, 7, 0, tzinfo=UTC)


def _row(
    title: str,
    *,
    amira: bool = False,
    themes: list[str] | None = None,
    state: str = NATIONAL,
    day: int = 20,
    link: str = "https://example.com/a",
) -> dict[str, object]:
    return {
        "lane": "vendor" if amira else "category",
        "title": title,
        "link": link,
        "themes": themes or [],
        "amira": amira,
        "published": datetime(2026, 8, day, tzinfo=UTC),
        "state": state,
    }


class TestEmptyResult:
    def test_empty_says_so_without_implying_an_outage(self) -> None:
        text = compose_brand_brief([], now=NOW)
        assert "No qualifying coverage" in text
        assert "not an outage" in text
        assert str(LOOKBACK_DAYS) in text

    def test_empty_brief_has_no_fabricated_sections(self) -> None:
        text = compose_brand_brief([], now=NOW)
        for heading in ("Amira by name", "Institutional action", "Pattern to watch"):
            assert heading not in text


class TestBottomLine:
    def test_counts_are_accurate(self) -> None:
        findings = [
            _row("Amira story", amira=True),
            _row("District drops the tool", themes=[THEME_INSTITUTIONAL_REJECTION]),
            _row("Parents object", themes=[THEME_PARENT_OBJECTION]),
        ]
        text = compose_brand_brief(findings, now=NOW)
        assert "3 qualifying stories" in text
        assert "1 names Amira directly" in text
        assert "1 involves a district" in text

    def test_states_are_ranked_and_national_is_excluded(self) -> None:
        findings = [
            _row("a", state="NM", link="1"),
            _row("b", state="NM", link="2"),
            _row("c", state="FL", link="3"),
            _row("d", state=NATIONAL, link="4"),
        ]
        text = compose_brand_brief(findings, now=NOW)
        assert "NM 2" in text
        assert "FL 1" in text
        assert f"{NATIONAL} " not in text.split("States named")[1].split("\n")[0]

    def test_no_state_named_reads_as_none_yet(self) -> None:
        text = compose_brand_brief([_row("a")], now=NOW)
        assert "none yet" in text


class TestSections:
    def test_amira_named_story_is_promoted_not_buried(self) -> None:
        findings = [
            _row("Ordinary category piece", themes=[THEME_PARENT_OBJECTION], link="1"),
            _row("Meet Amira, an AI reading tutor", amira=True, link="2"),
        ]
        text = compose_brand_brief(findings, now=NOW)
        assert text.index("Amira by name") < text.index("Category backdrop")

    def test_a_story_appears_in_exactly_one_section(self) -> None:
        """An Amira story that is also institutional must not be listed twice."""
        findings = [
            _row(
                "Santa Fe rejects Amira",
                amira=True,
                themes=[THEME_INSTITUTIONAL_REJECTION],
                link="https://example.com/santafe",
            )
        ]
        text = compose_brand_brief(findings, now=NOW)
        assert text.count("https://example.com/santafe") == 1

    def test_state_is_shown_when_known_and_omitted_when_national(self) -> None:
        known = compose_brand_brief([_row("x", state="NM")], now=NOW)
        assert "· NM —" in known
        national = compose_brand_brief([_row("x", state=NATIONAL)], now=NOW)
        assert "· US —" not in national

    def test_pipe_in_a_headline_cannot_break_slack_link_syntax(self) -> None:
        text = compose_brand_brief([_row("Schools | parents balk")], now=NOW)
        assert "Schools - parents balk" in text


class TestPeerPattern:
    def _iready(self, n: int) -> list[dict[str, object]]:
        titles = [
            "Lawsuit alleges i-Ready collected student data",
            "District shortens i-Ready contract",
            "CMS board questions iReady use",
            "Parents push back against i-Ready",
        ]
        return [_row(t, link=f"link{i}") for i, t in enumerate(titles[:n])]

    def test_dominant_peer_is_surfaced_with_its_escalation_ladder(self) -> None:
        text = compose_brand_brief(self._iready(4), now=NOW)
        assert "Pattern to watch — i-Ready" in text
        assert "*Litigation*" in text
        assert "*Contract*" in text
        assert "*Board Action*" in text

    def test_thin_peer_coverage_makes_no_claim(self) -> None:
        """Two stories is not a pattern; the section must stay silent."""
        assert "Pattern to watch" not in compose_brand_brief(self._iready(2), now=NOW)


class TestCaveat:
    def test_every_brief_states_what_it_cannot_see(self) -> None:
        for findings in ([], [_row("a")]):
            text = compose_brand_brief(findings, now=NOW)
            if findings:
                assert "Facebook parent groups" in text
                assert "Reddit" in text

    def test_caveat_is_last(self) -> None:
        text = compose_brand_brief([_row("a")], now=NOW)
        assert "does not cover" in text.split("\n")[-1]

    def test_singular_counts_read_grammatically(self) -> None:
        text = compose_brand_brief([_row("a")], now=NOW)
        assert "1 qualifying story in" in text
        assert "qualifying stories" not in text


class TestNoDuplicateLinks:
    def test_a_link_is_printed_at_most_once_across_all_sections(self) -> None:
        """The peer-pattern section re-frames stories that may already appear
        above it. Printing the same URL twice reads as padding and hides how
        much distinct coverage there actually is."""
        findings = [
            _row("Amira named piece", amira=True, link="https://x/1"),
            _row(
                "District shortens i-Ready contract",
                themes=[THEME_INSTITUTIONAL_REJECTION],
                link="https://x/2",
            ),
            _row("Lawsuit alleges i-Ready shared data", link="https://x/3"),
            _row("CMS board questions iReady use", link="https://x/4"),
            _row("Parents push back against i-Ready", link="https://x/5"),
        ]
        text = compose_brand_brief(findings, now=NOW)
        for n in range(1, 6):
            assert text.count(f"https://x/{n}") <= 1, f"link {n} printed twice"


class TestSlackSplitting:
    """Slack silently splits any message over ~4000 chars. The first live post
    of this brief arrived as three fragments each starting mid-list."""

    def test_short_text_is_one_part(self) -> None:
        assert split_for_slack("hello") == ["hello"]

    def test_long_text_splits_on_section_boundaries(self) -> None:
        section = "*Heading*\n" + "\n".join(f"• item {i}" for i in range(40))
        text = "\n\n".join([section] * 6)
        parts = split_for_slack(text, limit=800)
        assert len(parts) > 1
        for part in parts:
            assert part.startswith("*Heading*"), "a part must not begin mid-list"

    def test_every_part_is_within_the_limit_when_sections_allow(self) -> None:
        text = "\n\n".join(["x" * 300] * 20)
        assert all(len(p) <= 800 for p in split_for_slack(text, limit=800))

    def test_no_content_is_lost(self) -> None:
        text = "\n\n".join(f"section {i} " + "y" * 200 for i in range(15))
        parts = split_for_slack(text, limit=700)
        for i in range(15):
            assert f"section {i} " in "\n\n".join(parts)

    def test_an_oversized_single_section_is_emitted_whole(self) -> None:
        """Better one Slack-split long section than a link cut in half."""
        giant = "z" * 5000
        parts = split_for_slack(f"small\n\n{giant}", limit=1000)
        assert giant in parts


def test_scope_note_survives_into_the_main_slack_message() -> None:
    """The full caveat lands in a thread reply once the brief is long. A reader
    must not have to expand a thread to learn what the feed cannot see."""
    findings = [_row(f"story {i}", link=f"https://x/{i}") for i in range(40)]
    main = split_for_slack(compose_brand_brief(findings, now=NOW))[0]
    assert "Scope: news coverage only" in main


class TestScheduleRegistration:
    def test_job_registers_with_a_stable_id(self) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]

        from artemis.sentiment.report import register_brand_signals_schedule

        scheduler = AsyncIOScheduler()
        register_brand_signals_schedule(scheduler)
        job = scheduler.get_job("sentiment.brand_signals.daily")
        assert job is not None

    def test_registration_replaces_rather_than_duplicates(self) -> None:
        """Registering twice must leave one job under the id, not two.

        Asserted through ``add_job`` rather than ``get_jobs()``: on an
        unstarted scheduler APScheduler holds jobs in a pending queue that
        ``replace_existing`` does not collapse until start, so counting jobs
        here would test APScheduler's internals rather than our call.
        """
        from artemis.sentiment.report import register_brand_signals_schedule

        calls: list[dict[str, object]] = []

        class _Recorder:
            def add_job(self, _func: object, **kwargs: object) -> None:
                calls.append(kwargs)

        register_brand_signals_schedule(_Recorder())
        register_brand_signals_schedule(_Recorder())
        assert len(calls) == 2
        assert all(c["replace_existing"] is True for c in calls)
        assert {c["id"] for c in calls} == {"sentiment.brand_signals.daily"}
        assert all(c["max_instances"] == 1 for c in calls)

    def test_day_of_week_is_named_not_numeric(self) -> None:
        """This repo's APScheduler gotcha: numeric day-of-week is off by one."""
        from artemis.config import settings

        dow = settings.brand_signals_cron.split()[4]
        assert not dow.strip("*").replace("-", "").replace(",", "").isdigit(), (
            f"day-of-week {dow!r} must be by NAME (mon-fri), never numeric"
        )


class TestNewSinceLastBrief:
    """The reason the corpus exists. The first version re-listed the whole
    120-day window every morning; by day three there was nothing to read."""

    def test_new_section_is_absent_when_not_supplied(self) -> None:
        """Back-compat: the standing-only form must not grow an empty section."""
        text = compose_brand_brief([_row("a")], now=NOW)
        assert "New since the last brief" not in text

    def test_new_items_lead_the_brief(self) -> None:
        findings = [_row("Old standing story", link="https://x/old")]
        new = [_row("Brand new story", link="https://x/new")]
        text = compose_brand_brief(findings, new_items=new, corpus_total=9, now=NOW)
        assert text.index("New since the last brief") < text.index("Standing")
        assert text.index("https://x/new") < text.index("https://x/old")

    def test_empty_new_list_says_so_explicitly(self) -> None:
        """Silence must never be indistinguishable from an outage."""
        text = compose_brand_brief([_row("a")], new_items=[], now=NOW)
        assert "New since the last brief" in text
        assert "Nothing new" in text

    def test_long_new_list_is_capped_with_a_remainder_note(self) -> None:
        new = [_row(f"n{i}", link=f"https://n/{i}") for i in range(14)]
        text = compose_brand_brief([], new_items=new, now=NOW)
        assert "and 4 more new today" in text

    def test_corpus_total_is_reported_when_supplied(self) -> None:
        text = compose_brand_brief([_row("a")], corpus_total=412, now=NOW)
        assert "412 stories tracked" in text

    def test_standing_heading_absent_without_a_corpus_total(self) -> None:
        assert "Standing picture" not in compose_brand_brief([_row("a")], now=NOW)


class TestRowToDict:
    def test_maps_orm_fields_onto_the_composer_shape(self) -> None:
        class _Row:
            id = 7
            lane = "vendor"
            title = "Story"
            link = "https://x/1"
            themes = ["parent_objection"]
            names_amira = True
            published_at = datetime(2026, 8, 20, tzinfo=UTC)
            state = "NM"

        got = row_to_dict(_Row())
        assert got["amira"] is True
        assert got["published"] == datetime(2026, 8, 20, tzinfo=UTC)
        assert got["state"] == "NM"
        assert got["id"] == 7

    def test_null_themes_become_an_empty_list(self) -> None:
        class _Row:
            id = 1
            lane = "category"
            title = "t"
            link = "l"
            themes = None
            names_amira = False
            published_at = None
            state = "US"

        assert row_to_dict(_Row())["themes"] == []

    def test_the_result_renders_through_the_composer(self) -> None:
        """The adapter is only correct if the composer accepts its output."""

        class _Row:
            id = 1
            lane = "vendor"
            title = "Amira story"
            link = "https://x/1"
            themes: list[str] = []
            names_amira = True
            published_at = datetime(2026, 8, 20, tzinfo=UTC)
            state = "NM"

        text = compose_brand_brief([row_to_dict(_Row())], now=NOW)
        assert "Amira story" in text
        assert "1 names Amira directly" in text


def test_new_items_survive_an_empty_standing_window() -> None:
    """A brand-new story on a day the window happens to be empty must still be
    reported. Guarding only on `findings` discarded exactly the news."""
    new = [_row("Brand new", link="https://x/new")]
    text = compose_brand_brief([], new_items=new, now=NOW)
    assert "Brand new" in text
    assert "No qualifying coverage" not in text
