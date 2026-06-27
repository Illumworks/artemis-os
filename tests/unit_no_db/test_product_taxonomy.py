"""Unit tests for the Amira product taxonomy — the single source of truth that
translates "suite + function" phrasing into the product name assets are filed under.

Regression anchor: Sara's "video of a Lectura ILP lesson" returned nothing because
the asset is filed as "Enseñar" and the word "Lectura" appears nowhere on it.
"""

from __future__ import annotations

from artemis.enablement.product_taxonomy import expand_query, glossary_text


class TestExpandQuery:
    def test_lectura_ilp_appends_ensenar(self) -> None:
        """The core fix: Lectura (Spanish) + ILP (instruction) -> Enseñar."""
        out = expand_query("video of a Lectura ILP lesson")
        assert "Enseñar" in out
        assert out.startswith("video of a Lectura ILP lesson")  # original preserved

    def test_english_ilp_appends_instruct(self) -> None:
        out = expand_query("English ILP deck")
        assert "Instruct" in out

    def test_lectura_tutoring_appends_tutora(self) -> None:
        assert "Tutora" in expand_query("lectura tutoring video")

    def test_reading_suite_assessment_appends_isip_assess(self) -> None:
        assert "ISIP Assess" in expand_query("Reading Suite assessment")

    def test_function_only_is_noop(self) -> None:
        """ILP with no language stays broad — we cannot disambiguate, so don't guess."""
        assert expand_query("ILP video") == "ILP video"

    def test_suite_only_is_noop(self) -> None:
        assert expand_query("Lectura overview") == "Lectura overview"

    def test_unrelated_query_is_noop(self) -> None:
        assert expand_query("getting started deck") == "getting started deck"

    def test_empty_query_is_safe(self) -> None:
        assert expand_query("") == ""
        assert expand_query("   ") == "   "

    def test_accent_insensitive(self) -> None:
        """'ensenar' (no tilde) still recognized as the instruction product."""
        # An asker who types the suite + the product without the tilde.
        out = expand_query("lectura ensenar lesson")
        assert "Enseñar" in out


class TestGlossaryText:
    def test_names_all_six_products(self) -> None:
        text = glossary_text()
        for product in (
            "ISIP Assess",
            "Instruct",
            "Tutor",
            "ISIP Evaluar",
            "Tutora",
            "Enseñar",
        ):
            assert product in text

    def test_states_lectura_ilp_equals_ensenar(self) -> None:
        text = glossary_text().lower()
        assert "lectura ilp" in text
        assert "enseñar" in text

    def test_mentions_story_of_america_as_standalone(self) -> None:
        assert "Story of America" in glossary_text()
