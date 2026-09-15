"""RFC 2606 reserved addresses are guaranteed non-deliveries, not weak contacts."""

from __future__ import annotations

import pytest

from artemis.marketing.deliverable import is_deliverable


@pytest.mark.parametrize(
    "email",
    [
        # The six addresses actually in production on 2026-09-15, all left by tests.
        "alex.johnson@fort-worth-isd.example",
        "morgan.smith@ci4-e2e-fort-bend-isd-v2.example",
        "alex.johnson@ci4-live-e2e-fort-bend-isd-v3.example",
        "a@example.com",
        "a@example.org",
        "b@something.test",
        "c@thing.invalid",
        "d@localhost",
    ],
)
def test_reserved_addresses_are_not_deliverable(email: str) -> None:
    assert not is_deliverable(email)


@pytest.mark.parametrize(
    "email",
    [
        "jsmith@dallasisd.org",
        "curriculum@pinellas.k12.fl.us",
        "a.b@cps.edu",
        "SUPT@Examplewood.org",  # "example" inside a name is not a reserved domain
    ],
)
def test_real_addresses_are_deliverable(email: str) -> None:
    assert is_deliverable(email)


@pytest.mark.parametrize("email", [None, "", "   ", "notanemail", "a@b", "@b.org", "a@"])
def test_malformed_input_is_not_deliverable(email: str | None) -> None:
    assert not is_deliverable(email)


def test_case_and_whitespace_do_not_slip_through() -> None:
    assert not is_deliverable("  Alex@Fort-Worth-ISD.EXAMPLE  ")


def test_the_rule_is_not_a_general_validity_check() -> None:
    """Deliberately narrow: this rejects only what cannot resolve by definition.
    A typo'd domain is the transport's bounce reporting to catch, not this — and
    a rule that grew into address validation would start dropping real people."""
    assert is_deliverable("someone@dalasisd.org")  # misspelled, but a real domain shape
