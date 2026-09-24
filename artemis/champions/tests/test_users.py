"""The Users and Never Logged In tabs.

Hannah's column labels are verbatim and the counting rules are the point: each
of the three below was measured against the live community, and each of them is
wrong in a way that looks perfectly reasonable in code review.
"""

from __future__ import annotations

from typing import Any

from artemis.champions.users import USER_HEADERS, _row, is_educator, to_grid


def _user(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "email": "teacher@rusd.org",
        "name": "someone",
        "countDiscussions": 0,
        "countComments": 0,
        "countVisits": 0,
        "dateLastActive": "2026-09-11T10:00:00+00:00",
    }
    base.update(kw)
    return base


class TestWhoCounts:
    def test_amira_staff_are_excluded(self) -> None:
        """Hannah alone has 48 discussions and 48 comments; she would top the
        ranking of a tab meant to show the community."""
        assert not is_educator(_user(email="hannah.slater@amiralearning.com"))
        assert not is_educator(_user(email="someone@istation.com"))

    def test_vanillas_own_accounts_are_excluded(self) -> None:
        assert not is_educator(_user(name="System", email="system@vanillaforums.com"))
        assert not is_educator(_user(name="StopForumSpam", email="x@domain.com"))

    def test_an_educator_counts(self) -> None:
        assert is_educator(_user())

    def test_an_account_with_no_email_is_not_a_row(self) -> None:
        assert not is_educator(_user(email=""))


class TestPostsAreDiscussionsOnly:
    def test_posts_come_from_count_discussions(self) -> None:
        """Vanilla's countPosts is discussions PLUS comments -- verified across
        all 1,223 accounts -- so using it double-counts every commenter beside
        its own Comments column."""
        row = _row(_user(countDiscussions=4, countComments=18, countVisits=7), None)
        assert row["# of Posts"] == 4
        assert row["# of Comments"] == 18

    def test_count_posts_field_is_ignored(self) -> None:
        row = _row(_user(countDiscussions=4, countComments=18, countVisits=7, countPosts=22), None)
        assert row["# of Posts"] == 4


class TestActiveIsNotLoginsAlone:
    def test_someone_who_posted_with_zero_visits_is_active(self) -> None:
        """Fourteen real accounts look like this, one as recently as 2026-09-08.
        Filtering on logins alone would list them as never-logged-in and send a
        CSM chasing somebody who is actively participating."""
        row = _row(_user(countComments=3, countVisits=0), None)
        assert row["Active"] == "TRUE"

    def test_no_trace_at_all_is_inactive(self) -> None:
        assert _row(_user(), None)["Active"] == "FALSE"

    def test_logins_alone_makes_someone_active(self) -> None:
        assert _row(_user(countVisits=2), None)["Active"] == "TRUE"


class TestMostRecentLogin:
    def test_blank_when_there_are_no_logins(self) -> None:
        """dateLastActive is the CREATION date for an account that never logged
        in -- it equals dateInserted for 668 of 670 -- and printing it under
        "Most Recent Login" says somebody logged in when they never have."""
        assert _row(_user(countVisits=0), None)["Most Recent Login"] == ""

    def test_shown_when_they_have_logged_in(self) -> None:
        assert _row(_user(countVisits=5), None)["Most Recent Login"] == "2026-09-11"


class TestGrid:
    def test_header_order_is_hannahs(self) -> None:
        grid = to_grid([_row(_user(), None)])
        assert grid[0] == USER_HEADERS
        assert grid[0][5] == "# of Logins", "her label, not a tidier one"

    def test_every_row_matches_the_header_width(self) -> None:
        grid = to_grid([_row(_user(), None), _row(_user(countVisits=3), None)])
        assert all(len(r) == len(USER_HEADERS) for r in grid)
