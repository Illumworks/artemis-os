"""Repo-root pytest configuration.

Applies to every test package, which is the point: the fixture below has to
cover anything that writes a signal, and those tests live in several packages.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _stub_source_url_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep signal writes offline by default.

    ``signal_queue.write`` verifies that a signal's source URL resolves to a real
    page (added 2026-09-04, after a scout invented 149 of them). That is a real
    network call. Letting every test make one turned a 4-second suite into a
    225-second one and quietly made the tests depend on the internet — and on
    example.com's status code, which is what test fixtures use.

    Defined at the ROOT rather than per package so a test written later, in a
    package that does not exist yet, cannot reintroduce the problem.

    The verifier's own behaviour is covered directly in
    ``artemis/tools/tests/test_source_url_check.py`` against a stubbed transport,
    and a test that wants a rejection can patch
    ``artemis.tools.signal_queue.verify_source_url`` itself.
    """
    from artemis.tools._source_url_check import UrlVerdict

    async def _confirmed(_url: str) -> UrlVerdict:
        return UrlVerdict(ok=True, verified=True, reason="stubbed in tests")

    monkeypatch.setattr("artemis.tools.signal_queue.verify_source_url", _confirmed)
