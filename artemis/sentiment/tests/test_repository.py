"""Tests for the Brand Signals corpus.

The merge-on-conflict rules get the most attention, because a story genuinely
arrives twice in different shapes and each rule protects information that would
otherwise be silently lost on the second sighting.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from artemis.sentiment.models import BrandSignalFinding, content_hash_for
from artemis.sentiment.repository import (
    count_all,
    count_unreported,
    mark_all_reported,
    mark_reported,
    standing_picture,
    unreported,
    upsert_findings,
    window_findings,
)

pytestmark = pytest.mark.asyncio


def _f(
    title, *, link="https://x/1", lane="category", state="US", themes=None, amira=False, days_ago=1
):
    return {
        "title": title,
        "link": link,
        "lane": lane,
        "state": state,
        "themes": themes or [],
        "amira": amira,
        "published": datetime.now(UTC) - timedelta(days=days_ago),
    }


async def _row(session, title):
    result = await session.execute(
        select(BrandSignalFinding).where(BrandSignalFinding.content_hash == content_hash_for(title))
    )
    return result.scalar_one()


class TestUpsert:
    async def test_first_insert_counts_as_new(self, db_session):
        inserted, refreshed = await upsert_findings(db_session, [_f("Story A")])
        assert (inserted, refreshed) == (1, 0)
        assert await count_all(db_session) == 1

    async def test_reinsert_is_a_refresh_not_a_duplicate(self, db_session):
        await upsert_findings(db_session, [_f("Story A")])
        inserted, refreshed = await upsert_findings(db_session, [_f("Story A")])
        assert (inserted, refreshed) == (0, 1)
        assert await count_all(db_session) == 1

    async def test_title_punctuation_variants_are_one_story(self, db_session):
        await upsert_findings(db_session, [_f("Schools balk at A.I. testing")])
        await upsert_findings(db_session, [_f("Schools balk at A I testing!")])
        assert await count_all(db_session) == 1

    async def test_a_changed_link_does_not_create_a_second_row(self, db_session):
        """Google News links are opaque redirects that may be regenerated. If
        the link were the dedup key, the same story would land again daily."""
        await upsert_findings(db_session, [_f("Story A", link="https://news/abc")])
        await upsert_findings(db_session, [_f("Story A", link="https://news/DIFFERENT")])
        assert await count_all(db_session) == 1
        assert (await _row(db_session, "Story A")).link == "https://news/DIFFERENT"

    async def test_duplicates_within_one_batch_are_collapsed(self, db_session):
        """ON CONFLICT cannot see a second conflicting row in the same
        statement -- it errors. The same story arrives under several state
        queries in a single scan."""
        batch = [_f("Story A", state="NM"), _f("Story A", state="GA")]
        inserted, _ = await upsert_findings(db_session, batch)
        assert inserted == 1

    async def test_empty_input_is_a_no_op(self, db_session):
        assert await upsert_findings(db_session, []) == (0, 0)


class TestMergeRules:
    async def test_amira_flag_is_never_lost(self, db_session):
        await upsert_findings(db_session, [_f("Story A", amira=True)])
        await upsert_findings(db_session, [_f("Story A", amira=False)])
        assert (await _row(db_session, "Story A")).names_amira is True

    async def test_amira_flag_can_be_gained(self, db_session):
        await upsert_findings(db_session, [_f("Story A", amira=False)])
        await upsert_findings(db_session, [_f("Story A", amira=True)])
        assert (await _row(db_session, "Story A")).names_amira is True

    async def test_vendor_lane_wins_and_never_downgrades(self, db_session):
        await upsert_findings(db_session, [_f("Story A", lane="vendor")])
        await upsert_findings(db_session, [_f("Story A", lane="category")])
        assert (await _row(db_session, "Story A")).lane == "vendor"

    async def test_category_upgrades_to_vendor(self, db_session):
        await upsert_findings(db_session, [_f("Story A", lane="category")])
        await upsert_findings(db_session, [_f("Story A", lane="vendor")])
        assert (await _row(db_session, "Story A")).lane == "vendor"

    async def test_a_resolved_state_survives_a_later_national_sighting(self, db_session):
        """'US' means "no state could be resolved" -- it must never overwrite a
        real one, or a signal silently leaves the market it belongs to."""
        await upsert_findings(db_session, [_f("Story A", state="NM")])
        await upsert_findings(db_session, [_f("Story A", state="US")])
        assert (await _row(db_session, "Story A")).state == "NM"

    async def test_national_is_replaced_once_a_state_resolves(self, db_session):
        await upsert_findings(db_session, [_f("Story A", state="US")])
        await upsert_findings(db_session, [_f("Story A", state="FL")])
        assert (await _row(db_session, "Story A")).state == "FL"

    async def test_richer_theme_set_wins(self, db_session):
        await upsert_findings(db_session, [_f("Story A", themes=["parent_objection"])])
        await upsert_findings(
            db_session,
            [_f("Story A", themes=["parent_objection", "institutional_rejection"])],
        )
        assert len((await _row(db_session, "Story A")).themes) == 2

    async def test_poorer_theme_set_does_not_erase(self, db_session):
        await upsert_findings(
            db_session, [_f("Story A", themes=["parent_objection", "screen_time_harm"])]
        )
        await upsert_findings(db_session, [_f("Story A", themes=[])])
        assert len((await _row(db_session, "Story A")).themes) == 2

    async def test_first_seen_is_not_overwritten(self, db_session):
        await upsert_findings(db_session, [_f("Story A")])
        original = (await _row(db_session, "Story A")).first_seen_at
        await upsert_findings(db_session, [_f("Story A")])
        assert (await _row(db_session, "Story A")).first_seen_at == original

    async def test_last_seen_advances_on_a_repeat_sighting(self, db_session):
        await upsert_findings(db_session, [_f("Story A")])
        first = (await _row(db_session, "Story A")).last_seen_at
        await upsert_findings(db_session, [_f("Story A")])
        assert (await _row(db_session, "Story A")).last_seen_at >= first


class TestReportedMarker:
    async def test_new_rows_start_unreported(self, db_session):
        await upsert_findings(db_session, [_f("Story A")])
        assert await count_unreported(db_session) == 1

    async def test_marking_removes_it_from_the_new_list(self, db_session):
        await upsert_findings(db_session, [_f("Story A")])
        row = await _row(db_session, "Story A")
        assert await mark_reported(db_session, [row.id]) == 1
        assert await count_unreported(db_session) == 0

    async def test_a_reported_story_stays_reported_when_seen_again(self, db_session):
        """The whole point: a story already briefed must not come back as new
        just because the feed returned it again tomorrow."""
        await upsert_findings(db_session, [_f("Story A")])
        row = await _row(db_session, "Story A")
        await mark_reported(db_session, [row.id])
        await upsert_findings(db_session, [_f("Story A")])
        assert await count_unreported(db_session) == 0

    async def test_marking_nothing_is_safe(self, db_session):
        assert await mark_reported(db_session, []) == 0

    async def test_unreported_is_newest_first(self, db_session):
        await upsert_findings(
            db_session,
            [
                _f("Old story", link="1", days_ago=30),
                _f("New story", link="2", days_ago=1),
            ],
        )
        titles = [r.title for r in await unreported(db_session)]
        assert titles[0] == "New story"

    async def test_unreported_respects_the_limit(self, db_session):
        await upsert_findings(db_session, [_f(f"Story {i}", link=str(i)) for i in range(10)])
        assert len(await unreported(db_session, limit=3)) == 3

    async def test_backfill_marks_the_whole_corpus(self, db_session):
        await upsert_findings(db_session, [_f(f"Story {i}", link=str(i)) for i in range(5)])
        assert await mark_all_reported(db_session) == 5
        assert await count_unreported(db_session) == 0


class TestStandingPicture:
    async def test_counts_states_and_excludes_national(self, db_session):
        await upsert_findings(
            db_session,
            [
                _f("A", link="1", state="NM"),
                _f("B", link="2", state="NM"),
                _f("C", link="3", state="FL"),
                _f("D", link="4", state="US"),
            ],
        )
        picture = await standing_picture(db_session)
        assert picture["total"] == 4
        assert picture["states"][0] == ("NM", 2)
        assert dict(picture["states"]).get("US") is None

    async def test_counts_amira_named(self, db_session):
        await upsert_findings(db_session, [_f("A", link="1", amira=True), _f("B", link="2")])
        assert (await standing_picture(db_session))["amira"] == 1

    async def test_themes_are_tallied_across_rows(self, db_session):
        await upsert_findings(
            db_session,
            [
                _f("A", link="1", themes=["parent_objection"]),
                _f("B", link="2", themes=["parent_objection", "screen_time_harm"]),
            ],
        )
        themes = dict((await standing_picture(db_session))["themes"])
        assert themes["parent_objection"] == 2
        assert themes["screen_time_harm"] == 1

    async def test_window_excludes_older_stories_but_corpus_keeps_them(self, db_session):
        """The corpus outlives the query window -- that is the strategy asset."""
        await upsert_findings(
            db_session,
            [_f("Recent", link="1", days_ago=5), _f("Ancient", link="2", days_ago=400)],
        )
        picture = await standing_picture(db_session, days=120)
        assert picture["total"] == 1
        assert picture["corpus"] == 2

    async def test_empty_corpus_reports_zeroes_not_an_error(self, db_session):
        picture = await standing_picture(db_session)
        assert picture["total"] == 0 and picture["states"] == []


class TestWindowFindings:
    async def test_returns_window_newest_first(self, db_session):
        await upsert_findings(
            db_session,
            [
                _f("Old", link="1", days_ago=30),
                _f("New", link="2", days_ago=1),
                _f("Ancient", link="3", days_ago=400),
            ],
        )
        rows = await window_findings(db_session, days=120)
        assert [r.title for r in rows] == ["New", "Old"]

    async def test_reported_stories_still_appear_in_the_standing_picture(self, db_session):
        """Reported means "already announced as new", not "hidden" -- the
        standing sections are a reference view of everything in the window."""
        await upsert_findings(db_session, [_f("Story A")])
        await mark_all_reported(db_session)
        assert len(await window_findings(db_session)) == 1

    async def test_respects_the_limit(self, db_session):
        await upsert_findings(db_session, [_f(f"S{i}", link=str(i)) for i in range(10)])
        assert len(await window_findings(db_session, limit=4)) == 4
