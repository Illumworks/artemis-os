"""Retrieval fixes for the question Kai got wrong.

On 2026-08-26 Sara asked for "customer facing tech requirement documents for
rostering". The library holds exactly that — Amira Technical Guide, Tech Prep
Guide, Device Setup Guides, and the Clever / ClassLink walkthroughs — and Kai
would have returned the Tech Care Family Letter, a parent PDF.

Three causes, two fixed here:

1. Keyword search ran ONLY as a fallback when vector returned nothing, so a
   title containing the asker's own word never entered the pool.
2. The keyword pass matched the WHOLE query as one substring, so
   "%customer facing tech requirement documents for rostering%" matched nothing.
3. The word "roster" appears in ZERO of 416 assets — no search can find a word
   the corpus does not contain, which is what the domain vocabulary bridges.

The third is a content problem; 69% of assets have no summary at all, and that
is not fixable in code.
"""

from __future__ import annotations

from artemis.enablement.product_taxonomy import expand_domain_terms, expand_query


def test_rostering_maps_to_the_words_the_library_actually_uses() -> None:
    """THE 2026-08-26 case. Nothing is filed under "rostering"."""
    terms = expand_domain_terms("rostering resources")

    assert "Clever" in terms
    assert "ClassLink" in terms
    assert "log in" in terms


def test_tech_requirement_phrasings_map_to_the_real_titles() -> None:
    for phrasing in (
        "customer facing tech requirement documents",
        "what are the technical requirements",
        "system requirements for deployment",
        "IT requirements",
    ):
        terms = expand_domain_terms(phrasing)
        assert "Technical Guide" in terms, phrasing
        assert "Tech Prep" in terms, phrasing


def test_a_compound_question_gets_both_expansions() -> None:
    """Sara's actual sentence asked two things at once."""
    terms = expand_domain_terms("customer facing tech requirement documents for rostering")

    assert "Technical Guide" in terms
    assert "Clever" in terms


def test_sso_and_provisioning_are_the_same_concept_as_rostering() -> None:
    for phrasing in ("SSO setup", "provisioning guide", "account setup help"):
        assert "Clever" in expand_domain_terms(phrasing), phrasing


def test_an_unrelated_question_expands_to_nothing() -> None:
    """Every entry widens the pool for matching queries, so it must not over-fire."""
    for phrasing in (
        "parent letter about screen time",
        "winter coloring page",
        "training deck for coaches",
    ):
        assert expand_domain_terms(phrasing) == [], phrasing


def test_a_term_already_present_is_not_duplicated() -> None:
    """Asking for Clever by name should not append Clever again."""
    assert "Clever" not in expand_domain_terms("Clever rostering setup")


def test_empty_input_is_handled() -> None:
    assert expand_domain_terms("") == []
    assert expand_domain_terms("   ") == []


def test_the_product_expansion_is_untouched() -> None:
    """The domain map is additive — it must not disturb suite+function resolution."""
    assert expand_query("Lectura ILP") != "Lectura ILP", "product expansion still fires"
    assert expand_query("") == ""
