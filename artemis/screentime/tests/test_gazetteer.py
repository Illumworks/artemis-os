"""Tests for the place-name -> state index.

Every case here came from a REAL headline in the 2026-08-24 vendor sweep, not
from invented data -- which matters, because the bug this module is most likely
to reintroduce (San Diego -> TX) was invisible to reasoning and only appeared
when the index met live prose.
"""

from artemis.screentime.gazetteer import (
    DOMINANCE_RATIO,
    MIN_ENROLLMENT,
    build_place_index,
    find_places,
    normalize_place,
)


class TestNormalizePlace:
    def test_strips_structural_suffixes(self) -> None:
        assert normalize_place("SALAMANCA CITY SCHOOL DISTRICT") == "salamanca"
        assert normalize_place("KATY ISD") == "katy"
        assert normalize_place("Hillsborough County Public Schools") == "hillsborough"

    def test_keeps_hyphenated_compound_names(self) -> None:
        assert normalize_place("Charlotte-Mecklenburg Schools") == "charlotte-mecklenburg"

    def test_name_that_is_all_structure_reduces_to_nothing(self) -> None:
        assert normalize_place("Public Schools") == ""

    def test_handles_empty_and_junk(self) -> None:
        assert normalize_place("") == ""
        assert normalize_place("!!!") == ""


class TestBuildPlaceIndex:
    def test_unique_prominent_name_is_indexed(self) -> None:
        idx = build_place_index([("PINELLAS", "FL", 87_876)])
        assert idx == {"pinellas": "FL"}

    def test_small_district_never_claims_a_bare_name(self) -> None:
        """The San Diego bug, pinned.

        `districts` holds SAN DIEGO ISD, TX (1,453) and NOT San Diego Unified,
        CA. The token is unique, so uniqueness alone would resolve it -- to the
        wrong state. Uniqueness in an incomplete table is not correctness.
        """
        idx = build_place_index([("SAN DIEGO ISD", "TX", 1_453)])
        assert "san diego" not in idx

    def test_domain_vocabulary_is_never_a_place(self) -> None:
        """Reading, Massachusetts attributed a Florida story to MA."""
        idx = build_place_index([("Reading Public Schools", "MA", 900_000)])
        assert "reading" not in idx

    def test_ambiguous_name_across_states_is_omitted(self) -> None:
        idx = build_place_index([("Springfield", "IL", 60_000), ("Springfield", "MO", 55_000)])
        assert "springfield" not in idx, "a coin-flip must abstain, not guess"

    def test_dominant_state_wins_over_a_small_rival(self) -> None:
        rows = [
            ("HILLSBOROUGH", "FL", 220_000),
            ("Hillsborough Township Public School District", "NJ", 30_000),
        ]
        assert build_place_index(rows)["hillsborough"] == "FL"

    def test_dominance_is_a_ratio_not_a_maximum(self) -> None:
        """Just-below the ratio must abstain; at the ratio it resolves."""
        under = build_place_index([("Fairfax", "VA", 100_000), ("Fairfax", "CA", 30_000)])
        assert "fairfax" not in under
        over = build_place_index(
            [("Fairfax", "VA", int(30_000 * DOMINANCE_RATIO)), ("Fairfax", "CA", 30_000)]
        )
        assert over["fairfax"] == "VA"

    def test_missing_enrollment_is_not_treated_as_prominent(self) -> None:
        assert build_place_index([("Somewhere", "NV", None)]) == {}

    def test_enrollment_floor_is_enforced(self) -> None:
        assert build_place_index([("Barelybig", "OR", MIN_ENROLLMENT - 1)]) == {}
        assert build_place_index([("Barelybig", "OR", MIN_ENROLLMENT)]) == {"barelybig": "OR"}

    def test_bad_state_codes_are_skipped(self) -> None:
        assert build_place_index([("Someplace", "", 90_000)]) == {}
        assert build_place_index([("Someplace", "Texas", 90_000)]) == {}


class TestFindPlaces:
    IDX = {"hillsborough": "FL", "pinellas": "FL", "charlotte-mecklenburg": "NC", "katy": "TX"}

    def test_resolves_a_place_named_without_its_state(self) -> None:
        text = "i-Ready, used in Hillsborough County, faces lawsuit over student data"
        assert find_places(text, self.IDX) == {"FL"}

    def test_matches_whole_words_only(self) -> None:
        """'katy' must not fire inside 'Katydid' or a surname."""
        assert find_places("Katydid Elementary opened Monday", self.IDX) == set()

    def test_story_with_no_place_resolves_to_nothing(self) -> None:
        text = "In Backlash Against Tech in Schools, Parents Are Winning Rollbacks"
        assert find_places(text, self.IDX) == set()

    def test_two_places_return_both_states(self) -> None:
        got = find_places("Pinellas and Charlotte-Mecklenburg both paused the program", self.IDX)
        assert got == {"FL", "NC"}

    def test_empty_text_is_safe(self) -> None:
        assert find_places("", self.IDX) == set()

    def test_longest_match_wins_over_its_own_fragment(self) -> None:
        """A real miss: "Charlotte-Mecklenburg" (NC) contains "charlotte",
        which is separately Charlotte County, FL -- so one North Carolina story
        resolved to NC *and* FL."""
        idx = {"charlotte-mecklenburg": "NC", "charlotte": "FL"}
        text = "Charlotte-Mecklenburg Schools shortens i-Ready contract over screen time"
        assert find_places(text, idx) == {"NC"}

    def test_fragment_still_matches_when_it_stands_alone(self) -> None:
        idx = {"charlotte-mecklenburg": "NC", "charlotte": "FL"}
        assert find_places("Charlotte County schools paused the rollout", idx) == {"FL"}

    def test_token_contained_in_another_states_token_is_dropped(self) -> None:
        """The Charlotte case.

        "charlotte" is Charlotte County, FL; "charlotte-mecklenburg" is
        Charlotte, NC. A Charlotte Observer story about the NC board never
        writes "Mecklenburg", so it matched the FL token and was filed in
        Florida. Longest-match at lookup cannot fix it -- the longer token is
        absent from the text -- so the ambiguity has to be resolved at build
        time by refusing to index the fragment at all.
        """
        idx = build_place_index(
            [
                ("Charlotte County Public Schools", "FL", 17_000),
                ("Charlotte-Mecklenburg Schools", "NC", 141_000),
            ]
        )
        assert "charlotte" not in idx
        assert idx["charlotte-mecklenburg"] == "NC"

    def test_fragment_survives_when_both_are_the_same_state(self) -> None:
        """Only a CROSS-STATE containment is ambiguous."""
        idx = build_place_index(
            [("Aurora Public Schools", "CO", 39_000), ("Aurora West", "CO", 12_000)]
        )
        assert idx["aurora"] == "CO"
