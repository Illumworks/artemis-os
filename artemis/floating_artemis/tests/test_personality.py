"""Tests for the personality module — profile loading, voice corpus, and system-prompt wiring."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

from artemis.floating_artemis import personality as pm
from artemis.floating_artemis.chat import _build_system_prompt

# ── _load_profile ─────────────────────────────────────────────────────────────


def test_personality_profile_non_empty() -> None:
    """PERSONALITY_PROFILE is loaded from disk and contains real content."""
    assert pm.PERSONALITY_PROFILE, "Expected non-empty personality profile"
    assert "Artemis" in pm.PERSONALITY_PROFILE


def test_load_agent_profile_artemis_compatibility() -> None:
    profile = pm.load_agent_profile("artemis")
    assert profile.agent_id == "artemis"
    assert profile.display_name == "Artemis"
    assert profile.persona_core == pm.ARTEMIS_PERSONA_CORE
    assert profile.profile_text == pm.PERSONALITY_PROFILE
    assert profile.voice_corpus == pm.VOICE_CORPUS


def test_load_agent_profile_callie_returns_committed_profile() -> None:
    profile = pm.load_agent_profile("callie")
    assert profile.agent_id == "callie"
    assert profile.display_name == "Callie"
    assert "marketing strategist and analyst" in profile.persona_core
    assert "bridge between signal and story" in profile.profile_text
    assert "Three signals worth your time. The rest I triaged." in profile.voice_corpus
    assert all("—" not in phrase and "–" not in phrase for phrase in profile.voice_corpus)


def test_load_profile_missing_file_returns_empty(tmp_path: Path) -> None:
    """_load_profile returns '' (not an exception) when the file is absent."""
    result = pm._load_profile(tmp_path / "does_not_exist.md")
    assert result == ""


def test_load_profile_reads_custom_path(tmp_path: Path) -> None:
    """_load_profile reads content from an arbitrary path."""
    fake = tmp_path / "profile.md"
    fake.write_text("Hello from profile", encoding="utf-8")
    assert pm._load_profile(fake) == "Hello from profile"


# ── VOICE_CORPUS ──────────────────────────────────────────────────────────────


def test_voice_corpus_non_empty() -> None:
    """VOICE_CORPUS should parse at least one phrase from the real profile."""
    assert len(pm.VOICE_CORPUS) >= 1, f"Expected phrases, got: {pm.VOICE_CORPUS!r}"
    assert len(pm.VOICE_CORPUS) >= 12


def test_voice_corpus_known_phrase() -> None:
    """A known phrase from the profile must appear in the corpus verbatim."""
    assert "Already on it." in pm.VOICE_CORPUS


def test_parse_voice_corpus_empty_text() -> None:
    """Parsing an empty string returns an empty list."""
    assert pm._parse_voice_corpus("") == []


def test_parse_voice_corpus_extracts_phrases() -> None:
    """Phrase extraction works on a synthetic profile snippet."""
    snippet = textwrap.dedent(
        """\
        **Characteristic phrases (natural to her voice):**
        - "Already on it."
        - "Done. You're welcome."
        - "Working on it. Try to stay calm."

        Other content here.
        """
    )
    result = pm._parse_voice_corpus(snippet)
    assert "Already on it." in result
    assert "Done. You're welcome." in result
    assert "Working on it. Try to stay calm." in result


# ── select_voice_samples ──────────────────────────────────────────────────────


def test_select_voice_samples_returns_k_items() -> None:
    """select_voice_samples returns exactly k items (when corpus is large enough)."""
    samples = pm.select_voice_samples(session_id="test-session-abc", k=4)
    assert len(samples) == 4


def test_select_voice_samples_returns_strings() -> None:
    """All returned items are strings."""
    samples = pm.select_voice_samples(session_id="s1", k=3)
    assert all(isinstance(s, str) for s in samples)


def test_select_voice_samples_deterministic() -> None:
    """Same session_id → identical samples on repeated calls."""
    a = pm.select_voice_samples(session_id="deterministic-session", k=4)
    b = pm.select_voice_samples(session_id="deterministic-session", k=4)
    assert a == b


def test_select_voice_samples_varies_across_sessions() -> None:
    """Different session_ids should (statistically) yield different samples."""
    seen: set[tuple[str, ...]] = set()
    for i in range(100):
        sid = f"session-{i:03d}"
        samples = pm.select_voice_samples(session_id=sid, k=4)
        seen.add(tuple(samples))

    # With 100 different sessions we expect at least 2 distinct orderings.
    assert len(seen) > 1, "100 different session_ids all produced identical samples — unexpected"


def test_select_voice_samples_empty_corpus() -> None:
    """Returns [] when the corpus is empty."""
    with patch.object(pm, "VOICE_CORPUS", []):
        result = pm.select_voice_samples(session_id="s", k=4)
    assert result == []


def test_select_voice_samples_clamps_to_corpus_size() -> None:
    """When k > corpus size, returns the full corpus (no error)."""
    with patch.object(pm, "VOICE_CORPUS", ["a", "b"]):
        result = pm.select_voice_samples(session_id="s", k=10)
    assert set(result) == {"a", "b"}


def test_select_voice_samples_uses_explicit_voice_corpus() -> None:
    result = pm.select_voice_samples(session_id="s", k=10, voice_corpus=["x", "y"])
    assert set(result) == {"x", "y"}


# ── _build_system_prompt: Slack assume-yes block ──────────────────────────────


def test_build_system_prompt_slack_session_has_assume_yes_block() -> None:
    """A slack- session_id injects the Slack assume-yes context block."""
    prompt = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=[],
        session_id="slack-T1-C1-_",
    )
    assert "You are responding in Slack" in prompt
    assert "Assume they are addressing you" in prompt
    assert "Are you talking to me?" in prompt


def test_build_system_prompt_non_slack_session_no_slack_block() -> None:
    """A non-Slack session_id must NOT include the Slack block."""
    prompt = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=[],
        session_id="fa-abc-123",
    )
    assert "You are responding in Slack" not in prompt


def test_build_system_prompt_default_session_id_no_slack_block() -> None:
    """Omitting session_id (default '') must NOT include the Slack block."""
    prompt = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=[],
    )
    assert "You are responding in Slack" not in prompt


# ── _build_system_prompt: full profile is included ────────────────────────────


def test_build_system_prompt_includes_full_profile() -> None:
    """The full personality profile body is appended to the system prompt."""
    prompt = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=[],
        session_id="fa-test",
    )
    # The profile mentions "chief of operations" — a verbatim phrase
    assert "chief of operations" in prompt
    # Presence of the profile heading
    assert "Full personality profile" in prompt


def test_build_system_prompt_voice_samples_are_calibration_only() -> None:
    prompt = _build_system_prompt(
        voice_samples=["Already on it."],
        page_context=None,
        available_surfaces=[],
        session_id="fa-test",
    )
    assert "Characteristic phrases (calibration only)" in prompt
    assert "Never quote them verbatim or near-verbatim." in prompt
    assert "Generate fresh lines in this spirit:" in prompt


def test_build_system_prompt_includes_no_dash_no_emoji_lint() -> None:
    prompt = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=[],
        session_id="fa-test",
    )
    assert "No em or en dashes. No emojis." in prompt


def test_build_system_prompt_artemis_profile_matches_legacy_output() -> None:
    profile = pm.load_agent_profile("artemis")

    def build_legacy_prompt() -> str:
        parts = [pm.ARTEMIS_PERSONA_CORE]
        if pm.PERSONALITY_PROFILE:
            parts.append(
                "## Full personality profile (background reference)\n" + pm.PERSONALITY_PROFILE
            )
        parts.append(
            "## Characteristic phrases (calibration only)\n"
            "These calibrate your register and rhythm. Never quote them verbatim or "
            'near-verbatim. Generate fresh lines in this spirit:\n- "Already on it."'
        )
        parts.append("## Current operator context\nPage: okr")
        parts.append("## Available surfaces (your tools are gated by these)\nokr")
        parts.append(
            "## Human communication rules (all agents, every turn)\n"
            "- Address people by their first name naturally when you know it — weave it "
            "in, don't open every message the same way.\n"
            "- Vary your sentence openings. Never start two consecutive messages the same way.\n"
            "- Never send canned, templated, or copy-pasted-sounding text.\n"
            "- Sound like a real person: concrete, warm where it fits, specific rather than generic."
        )
        parts.append(
            "## Acting means calling a tool (all agents, every turn)\n"
            "- To DO anything with an effect — schedule, send, dispatch, write, react — "
            "you must CALL the tool that does it. Describing the action in words is not "
            "performing it.\n"
            "- For side-effecting tools, calling is still safe: the system stages the call "
            "and hands you back a confirmation prompt to relay. Nothing executes until the "
            "operator confirms, so calling IS how you propose. Don't write a prose proposal "
            "instead of calling.\n"
            "- Never tell the operator something is done, or that you've started it (e.g. "
            '"it\'s on it", "I\'ve queued it"), unless you actually called the tool this turn.\n'
            '- Never claim a capability is missing or "not wired." If a tool is in your '
            "toolset, use it. If the call errors, report the actual error — don't invent a "
            "limitation."
        )
        parts.append(
            "**You are responding in Slack.** The operator @-mentioned you directly. "
            "**Assume they are addressing you and respond on-topic.** "
            'Do not ask "Are you talking to me?" — they are. '
            "Be concise; Slack rewards short replies. "
            "**Do NOT use markdown tables** (`| Field | Value |` pipe-table syntax) — "
            "Slack renders them as raw pipes and dashes. "
            "Use **bold labels** with short bullet lines or plain sentences instead."
            " The operator is Jon."
        )
        parts.append(
            "## Slack DM scope\n"
            "This 1:1 Slack DM is for personal support, app/ops issues, and upgrades. "
            "You are Jon's orchestrator and personal partner here. "
            "Do not volunteer marketing in this DM. Marketing is Callie's lane and only "
            "comes here if Jon explicitly asks."
        )
        return "\n\n".join(parts)

    prompt = _build_system_prompt(
        voice_samples=["Already on it."],
        page_context="Page: okr",
        available_surfaces=["okr"],
        persona_core=profile.persona_core,
        profile_text=profile.profile_text,
        display_name=profile.display_name,
        agent_id=profile.agent_id,
        session_id="slack-T1-D123-_",
        speaker_name="Jon",
        is_personal_slack_dm=True,
    )
    assert prompt == build_legacy_prompt()
