"""Domain -> pod resolution.

The rule here is copied from the Worker's ``lookup()`` rather than reinvented,
and these tests pin the two parts of it that are load-bearing. Both exist
because of a specific way of being confidently wrong: a unique match in an
incomplete table reads exactly like a correct one.
"""

from __future__ import annotations

from artemis.champions.pods import CONSUMER_DOMAINS, PodMatch, _to_match, resolve


def _row(**kw: object) -> dict[str, object]:
    base = {
        "pod_slug": "new-mexico",
        "account_name": "Rio Rancho Public Schools",
        "state": "NM",
        "csm_email": "someone@amiralearning.com",
        "is_ambiguous": 0,
        "pod_name": "New Mexico",
    }
    base.update(kw)
    return base


class TestSettledMatches:
    def test_a_clean_domain_resolves_fully(self) -> None:
        m = _to_match("1.rrps.net", _row())
        assert m.district == "Rio Rancho Public Schools"
        assert m.state == "NM" and m.pod_name == "New Mexico"
        assert m.needs_decision is False


class TestRefusingToGuess:
    """Naming one arbitrary district is worse than admitting we do not know."""

    def test_ambiguous_domain_names_nothing(self) -> None:
        m = _to_match("dekalbschoolsga.org", _row(is_ambiguous=1))
        assert m.is_ambiguous is True
        assert m.needs_decision is True
        # Every field a reader might trust must be empty, not "the first one".
        assert m.district is None and m.state is None
        assert m.pod_slug is None and m.pod_name is None and m.csm_email is None

    def test_unassigned_pod_is_not_a_placement(self) -> None:
        m = _to_match("somewhere.edu", _row(pod_slug="unassigned"))
        assert m.needs_decision is True and m.pod_slug is None

    def test_null_pod_is_not_a_placement(self) -> None:
        m = _to_match("somewhere.edu", _row(pod_slug=None))
        assert m.needs_decision is True


class TestConsumerDomains:
    """gmail.com was recorded in Salesforce against a real Arizona district.

    Left in, it would have placed every personal-address Champion there.
    """

    def test_personal_addresses_are_never_placed(self) -> None:
        directory = {"gmail.com": _to_match("gmail.com", _row(account_name="Some AZ District"))}
        assert resolve("gmail.com", directory) is None

    def test_even_when_the_directory_wrongly_contains_one(self) -> None:
        """The guarantee must not depend on the build script staying correct."""
        for domain in ("yahoo.com", "icloud.com", "outlook.com"):
            directory = {domain: _to_match(domain, _row())}
            assert resolve(domain, directory) is None, domain

    def test_a_personal_address_is_not_a_decision_for_a_human(self) -> None:
        """It is a person, not a district. Putting it in the decision queue
        would bury the domains someone can actually fix."""
        assert "gmail.com" in CONSUMER_DOMAINS
        assert resolve("gmail.com", {}) is None


class TestLookupHygiene:
    def test_case_and_whitespace_do_not_matter(self) -> None:
        directory = {"rusd.org": _to_match("rusd.org", _row())}
        got = resolve("  RUSD.ORG ", directory)
        assert isinstance(got, PodMatch) and got.district is not None

    def test_unknown_domain_is_none(self) -> None:
        assert resolve("nowhere.example", {}) is None

    def test_empty_domain_is_none(self) -> None:
        assert resolve(None, {}) is None
        assert resolve("", {}) is None
