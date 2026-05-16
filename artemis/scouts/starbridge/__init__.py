"""Starbridge Researcher Scout — bench-test API integration.

This submodule implements the real Starbridge researcher scout. The API shape
is not yet confirmed with the Starbridge team; all ambiguous fields and
endpoints are marked with ``# TODO: confirm with Starbridge team``.

All API calls are tagged with ``bench_test_period=True`` in the request body
so usage can be tracked for the renewal decision.

The top-level stub at ``artemis/scouts/starbridge_researcher.py`` is kept for
D1 backward-compatibility. This submodule is the real implementation.
"""
