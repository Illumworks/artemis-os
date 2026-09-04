"""Hard rules belong above the profile body, not filed inside it.

Callie's profile carries three sections marked "(Hard Rule)". Every one was
written after it had already been broken in good faith: research described as
underway that was never dispatched, and then, on 2026-09-04, a page handed to
Josh to open that she could open herself in one tool call.

The whole profile was injected under the heading "Full personality profile
(background reference)" -- the same weight as tone, register and voice. That is
the wrong place for a rule that exists because it was broken.

The mechanism under test is the heading, not a list of section names. A new hard
rule is hoisted by writing "(Hard Rule)" in its heading, with no code change and
no list to keep in sync.
"""

from __future__ import annotations

import re

from artemis.floating_artemis.chat import _build_system_prompt
from artemis.floating_artemis.personality import extract_hard_rules, load_agent_profile

PROFILE = """# Agent

## Identity
Some background about who this agent is.

## Claims About Work In Flight (Hard Rule)
Never describe a dependency as moving unless it is.

### A sub-heading inside the rule
This detail belongs to the rule above it.

---

## Tone
Warm but direct.

### Writing Lints (Hard Rules)
- No emojis.

## Relationships
Reports to Artemis.
"""


def test_only_sections_marked_as_hard_rules_are_extracted() -> None:
    rules = extract_hard_rules(PROFILE)

    assert "Never describe a dependency as moving unless it is." in rules
    assert "No emojis." in rules
    assert "Warm but direct." not in rules, "tone is background, not binding"
    assert "Reports to Artemis." not in rules


def test_a_sub_heading_does_not_truncate_its_rule() -> None:
    """A rule may have sub-sections; only a same-or-higher heading ends it."""
    rules = extract_hard_rules(PROFILE)

    assert "This detail belongs to the rule above it." in rules
    assert "Warm but direct." not in rules, "the following ## must still end it"


def test_a_new_hard_rule_needs_no_code_change() -> None:
    """The heading is the whole mechanism -- there is no list to keep in sync."""
    extended = PROFILE + "\n## Look It Up Yourself (Hard Rule)\nFetch it rather than asking.\n"

    assert "Fetch it rather than asking." in extract_hard_rules(extended)


def test_a_profile_with_no_hard_rules_yields_nothing() -> None:
    assert extract_hard_rules("## Identity\nJust background.\n") == ""
    assert extract_hard_rules("") == ""


def test_every_marked_section_in_the_real_profile_is_hoisted() -> None:
    """Asserts the MECHANISM, not a count.

    The earlier form of this pinned the number at three and broke the moment a
    fourth rule was written -- which is the one thing that must stay cheap, since
    the whole point is that a new rule needs no code change. What matters is that
    nothing marked "(Hard Rule)" is left behind in the background body.
    """
    profile = load_agent_profile("callie")
    in_profile = set(re.findall(r"^#{2,4}\s+(.*\(Hard Rules?\))\s*$", profile.profile_text, re.M))
    hoisted = set(re.findall(r"^(.*\(Hard Rules?\))$", profile.hard_rules, re.M))

    assert in_profile, "the live profile should carry at least one marked rule"
    assert in_profile == hoisted, f"not hoisted: {in_profile - hoisted}"


def test_the_rules_written_after_a_failure_are_all_present() -> None:
    """Each of these exists because the behaviour it forbids already happened."""
    hoisted = load_agent_profile("callie").hard_rules

    for rule in ("Work In Flight", "Look It Up Yourself", "Numbers From The CRM"):
        assert rule in hoisted, rule


def test_the_rules_are_placed_above_the_background_reference() -> None:
    """Ordering IS the fix. Below the profile body they carry its weight."""
    profile = load_agent_profile("callie")
    prompt = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=["slack"],
        persona_core=profile.persona_core,
        profile_text=profile.profile_text,
        hard_rules=profile.hard_rules,
        display_name=profile.display_name,
        agent_id="callie",
    )

    binding = prompt.find("## Binding rules")
    background = prompt.find("## Full personality profile")

    assert binding != -1, "the hard rules must reach the prompt at all"
    assert binding < background, "binding rules must precede the background body"
    assert "Look It Up Yourself" in prompt[binding:background]


def test_the_rules_still_appear_in_the_profile_body_too() -> None:
    """Hoisting must not tear the document in half; it reads as one piece."""
    profile = load_agent_profile("callie")

    assert "Look It Up Yourself" in profile.profile_text


def test_an_agent_without_hard_rules_gets_no_empty_binding_block() -> None:
    """An empty "these are binding" heading teaches the model nothing."""
    prompt = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=["slack"],
        persona_core="Core.",
        profile_text="## Identity\nBackground only.",
        hard_rules="",
        display_name="Artemis",
        agent_id="artemis",
    )

    assert "## Binding rules" not in prompt
