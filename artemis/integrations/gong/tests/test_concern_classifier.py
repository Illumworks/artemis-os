"""Rule 4 as a gate rather than an intention.

This module is the only transcript read in the codebase. These tests exist to make
the four constraints in its docstring impossible to quietly lose, because the
thing being protected is that the people on those calls consented to a
conversation with a vendor and not to us keeping their words.
"""

from __future__ import annotations

import pytest

from artemis.integrations.gong.concern_classifier import (
    CONCERN_CATEGORIES,
    ConcernProfile,
    _chunks,
    _parse_categories,
    _require_local_adapter,
)

# ── 1. the output is an enum, so a quote has nowhere to travel ───────────────


def test_only_real_categories_survive() -> None:
    raw = "rostering, training"

    assert _parse_categories(raw) == {"rostering", "training"}


def test_a_model_that_quotes_has_the_quote_discarded() -> None:
    """The gate. A model ignoring "do not quote" produces a sentence, and the
    sentence matches no category, so nothing of it reaches the caller."""
    raw = (
        'The principal said "we could never get our third graders rostered before '
        'October and the teachers were furious about it" — this is rostering.'
    )

    result = _parse_categories(raw)

    assert result == {"rostering"}
    assert all(c in CONCERN_CATEGORIES for c in result)
    assert not any("furious" in c or "third graders" in c for c in result)


def test_an_invented_category_is_dropped() -> None:
    assert _parse_categories("staffing, morale, budget_politics") == set()


def test_the_profile_has_no_field_that_could_hold_a_sentence() -> None:
    """Structural: counts are ints and the only strings are the account name and
    category keys, which come from the enum."""
    import dataclasses

    profile = ConcernProfile(account_name="A District", calls_examined=9, counts={"rostering": 4})

    for f in dataclasses.fields(profile):
        if f.name in ("account_name",):
            continue
        value = getattr(profile, f.name)
        if isinstance(value, dict):
            assert all(k in CONCERN_CATEGORIES for k in value)
            assert all(isinstance(v, int) for v in value.values())
        else:
            assert isinstance(value, (int, bool))


# ── 2. it runs locally or it does not run ───────────────────────────────────


def test_a_hosted_adapter_is_refused() -> None:
    """A transcript sent to a hosted API is a district's conversation leaving the
    building. There is no configuration that permits it."""

    class _Anthropic:
        pass

    with pytest.raises(RuntimeError, match="only be classified by the local model"):
        _require_local_adapter(_Anthropic())


def test_the_local_adapter_is_accepted() -> None:
    from artemis.providers.lm_studio.adapter import LMStudioAdapter

    _require_local_adapter(LMStudioAdapter())


def test_there_is_no_fallback_to_a_hosted_provider() -> None:
    """A cascade would make sending the transcript out the automatic response to
    a local outage — the one failure mode that must not be automatic."""
    import inspect

    from artemis.integrations.gong import concern_classifier as mod

    src = inspect.getsource(mod)

    # The mechanisms that would route this somewhere else, named individually.
    # An earlier version of this test grepped for the word "fallback" and failed
    # on the docstring explaining why there is not one.
    for escape_hatch in (
        "complete_with_fallback",
        "resolve_adapter",
        "AnthropicAdapter",
        "ClaudeCodeAdapter",
        "GeminiAdapter",
        "OpenAIAdapter",
    ):
        assert escape_hatch not in src, f"{escape_hatch} could send a transcript off-box"


# ── 3. the metadata client stays unable to read a transcript ────────────────


def test_the_metadata_client_still_cannot_fetch_a_transcript() -> None:
    """The capability lives in ONE module. If it ever migrates onto the client
    every feature in the system holds it, and the audit surface stops being one
    file."""
    import inspect

    from artemis.integrations.gong import client

    assert "/v2/calls/transcript" not in inspect.getsource(client)


def test_the_transcript_endpoint_appears_in_exactly_one_module() -> None:
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    hits = [
        p
        for p in root.rglob("*.py")
        if "/v2/calls/transcript" in p.read_text(encoding="utf-8", errors="ignore")
        and "test" not in p.name
    ]

    assert [p.name for p in hits] == ["concern_classifier.py"]


# ── 4. chunking, so a long call still fits a small context ──────────────────


def test_a_long_transcript_is_split_small_enough_for_a_4k_context() -> None:
    """JIT loading gives a model its DEFAULT 4,096-token context, not its
    maximum, and a 35-minute call is ~9,450 tokens."""
    text = "\n".join(f"Sentence number {i} in a long call." for i in range(4000))

    chunks = _chunks(text)

    assert len(chunks) > 1
    assert all(len(c) <= 6000 for c in chunks)


def test_a_short_transcript_is_one_chunk() -> None:
    assert len(_chunks("A short call.\nTwo lines only.")) == 1


def test_a_single_enormous_line_is_still_split() -> None:
    """One unbroken monologue must not defeat the chunker and blow the context."""
    chunks = _chunks("x" * 20000)

    assert all(len(c) <= 6000 for c in chunks)


# ── what an empty result means ──────────────────────────────────────────────


def test_unavailable_is_not_no_concerns() -> None:
    out = ConcernProfile(account_name="A District", unavailable=True).describe()

    assert "UNKNOWN" in out
    assert "not 'no concerns'" in out


def test_no_category_found_is_not_a_happy_district() -> None:
    out = ConcernProfile(account_name="A District", calls_examined=9).describe()

    assert "not the same as a happy district" in out
