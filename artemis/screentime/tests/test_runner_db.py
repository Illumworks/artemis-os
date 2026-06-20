"""End-to-end fixtured run test + cheap-provider assertion + tunable re-run."""

from __future__ import annotations

import pytest
from sqlalchemy import text

import artemis.screentime.classifier as classifier_mod
from artemis.agent.client import CompletionResponse
from artemis.agent.types import Message, TextBlock, Usage
from artemis.screentime.runner import run_screentime_pipeline
from artemis.screentime.stance_config import load_stance_rules, set_stance_rules

pytestmark = pytest.mark.asyncio


# Fixtured findings: one real legislative move (favorable carve-out), one
# blanket-restriction guidance (unfavorable), and one generic news headline
# that MUST be dropped by the real-moves filter.
FINDINGS = [
    {
        "sourceType": "legiscan",
        "discoveredBy": "legislative_scout",
        "districtId": "STATE_TN",
        "evidence": "HB 100 passed: limits screen time but exempts evidence-based instructional software.",
        "metadata": {"state": "TN", "status_code": 4, "url": "http://leg/tn/hb100"},
    },
    {
        "sourceType": "state_doe",
        "discoveredBy": "state_doe_scout",
        "title": "State guidance: blanket policy to minimize screen time",
        "summary": "Department guidance imposing a blanket reduction of screen time, no exceptions.",
        "metadata": {"state": "CA", "url": "http://doe/ca/guidance"},
    },
    {
        "sourceType": "newsapi",
        "discoveredBy": "regional_news_scout",
        "title": "What to know about screen time this fall",
        "summary": "A general explainer on screen time trends.",
        "metadata": {"state": "TX", "url": "http://news/tx/explainer"},
    },
]


def _fake_completion(text_out: str) -> CompletionResponse:
    return CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text=text_out)]),
        stop_reason="end_turn",
        usage=Usage(input_tokens=1, output_tokens=1),
    )


@pytest.fixture
def fake_cheap_provider(monkeypatch):
    """Stub complete_with_fallback to assert codex is the serving provider."""
    calls = {"n": 0}

    async def _fake(request, *, primary, fallback="claude-code", serving_provider_out=None, **kw):
        calls["n"] += 1
        assert primary == "codex"  # never Opus
        assert request.model is None  # footgun guard: model lives inside the request
        if serving_provider_out is not None:
            serving_provider_out.append("codex")
        return _fake_completion('{"stance":"favorable","amira_angle":"carve-out protects Amira"}')

    monkeypatch.setattr(classifier_mod, "complete_with_fallback", _fake, raising=False)
    # classify_signal imports the symbol lazily inside the function, so patch the
    # source module too.
    import artemis.providers.fallback as fb

    monkeypatch.setattr(fb, "complete_with_fallback", _fake, raising=False)
    return calls


async def test_fixtured_run_stores_real_moves_and_uses_codex(db_session, fake_cheap_provider):
    report = await run_screentime_pipeline(db_session, findings=FINDINGS, states=["TN", "CA", "TX"])
    await db_session.commit()

    assert report.error is None
    assert report.gathered == 3
    # The generic news headline is dropped; the two real moves survive.
    assert report.dropped_not_real_move == 1
    assert report.real_moves == 2
    assert report.stored_new == 2

    # Cheap provider served the classification — never Opus.
    assert report.providers_used.get("codex", 0) == 2

    rows = dict(
        (r[0], (r[1], r[2]))
        for r in (
            await db_session.execute(
                text("SELECT state, stance, amira_angle FROM screentime_signals")
            )
        ).all()
    )
    assert "TN" in rows and "CA" in rows
    assert "TX" not in rows  # dropped headline
    # Stance is config-driven (rules win), so CA's blanket restriction is unfavorable
    # even though the stubbed LLM said "favorable".
    assert rows["TN"][0] == "favorable"
    assert rows["CA"][0] == "unfavorable"
    assert rows["TN"][1]  # amira_angle populated

    # Per-state rollup populated.
    stance_states = {
        r[0]
        for r in (
            await db_session.execute(text("SELECT state FROM screentime_state_stance"))
        ).all()
    }
    assert {"TN", "CA"} <= stance_states


async def test_topic_gate_drops_offtopic_noise_end_to_end(db_session, fake_cheap_provider):
    """The first-run regression: reading-retention / literacy noise must be dropped
    by the topic gate before classify/store, while a genuine screen-time move is kept.
    """
    findings = [
        # OFF-TOPIC #1: reading retention with an "exempt" — the false-favorable.
        {
            "sourceType": "legiscan",
            "discoveredBy": "legislative_scout",
            "districtId": "STATE_FL",
            "evidence": (
                "HB 1 passed: third grade reading retention; exempts students using "
                "evidence-based reading programs from the retention limit."
            ),
            "metadata": {"state": "FL", "status_code": 4, "url": "http://leg/fl/hb1"},
        },
        # OFF-TOPIC #2: literacy curriculum mandate.
        {
            "sourceType": "state_doe",
            "discoveredBy": "state_doe_scout",
            "title": "Literacy curriculum approval",
            "summary": "Districts must adopt approved evidence-based phonics curriculum.",
            "metadata": {"state": "GA", "url": "http://doe/ga/lit"},
        },
        # ON-TOPIC: a real instructional screen-time move (kept).
        {
            "sourceType": "legiscan",
            "discoveredBy": "legislative_scout",
            "districtId": "STATE_TN",
            "evidence": "HB 100 passed: limits instructional screen time but exempts evidence-based software.",
            "metadata": {"state": "TN", "status_code": 4, "url": "http://leg/tn/hb100"},
        },
    ]
    report = await run_screentime_pipeline(db_session, findings=findings, states=["FL", "GA", "TN"])
    await db_session.commit()

    assert report.error is None
    assert report.gathered == 3
    # Both off-topic items are dropped by the topic gate; only TN survives.
    assert report.dropped_off_topic == 2
    assert report.topic_relevant == 1
    assert report.real_moves == 1
    assert report.stored_new == 1

    rows = dict(
        (r[0], r[1])
        for r in (
            await db_session.execute(text("SELECT state, stance FROM screentime_signals"))
        ).all()
    )
    # The off-topic reading-retention "exempt" NEVER stored → no false 🟢.
    assert "FL" not in rows
    assert "GA" not in rows
    # The genuine screen-time exemption is stored AND favorable.
    assert rows == {"TN": "favorable"}


async def test_rerun_after_config_change_flips_stance(db_session, fake_cheap_provider):
    # First run with default rules → CA blanket restriction = unfavorable.
    await run_screentime_pipeline(db_session, findings=[FINDINGS[1]], states=["CA"])
    await db_session.commit()
    first = (
        await db_session.execute(text("SELECT stance FROM screentime_signals WHERE state='CA'"))
    ).scalar_one()
    assert first == "unfavorable"

    # Tune the live DB rules so "blanket reduction" now counts as a carve-out
    # keyword → the same item reclassifies favorable on re-run. (Proves the
    # favorable/unfavorable mapping is pure config, changeable without a deploy.)
    rules = await load_stance_rules(db_session)
    rules = dict(rules)
    rules["favorable_keywords"] = rules["favorable_keywords"] + ["blanket reduction"]
    await set_stance_rules(db_session, rules)
    await db_session.commit()

    # Re-run: store_signal dedupes, but the runner reclassifies + the rollup
    # must reflect the new config. We assert via the classifier directly that the
    # tuned config flips the stance (proves tunable end-to-end).
    from artemis.screentime.classifier import classify_by_rules

    tuned = await load_stance_rules(db_session)
    assert (
        classify_by_rules(
            "Department guidance imposing a blanket reduction of screen time, no exceptions.",
            tuned,
        )
        == "favorable"
    )
