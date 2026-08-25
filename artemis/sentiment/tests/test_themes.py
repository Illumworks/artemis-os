"""Unit tests for the parent-sentiment narrative-theme matcher (pure, no DB, no I/O).

Structure:
  - structural guard: every anchor is multi-word (no bare "ai"/"voice"-style
    tokens ever sneak back in).
  - one test per theme showing several genuinely different phrasings match.
  - false-positive guards: ordinary sentences that contain the theme's *topic
    words* but not its *narrative meaning* must NOT match. These are the
    point of the module — see topic_config.py's own documented reason for
    banning bare tokens.
  - the precision requirement: ``match_themes`` (topic signal) and
    ``is_amira_specific`` (brand signal) must answer independently, never as
    one merged boolean.
"""

from __future__ import annotations

import pytest

from artemis.sentiment.themes import (
    THEME_IS_A_CHATBOT,
    THEME_PARENT_OBJECTION,
    THEME_PRIVACY_SURVEILLANCE,
    THEME_SCREEN_TIME_HARM,
    THEME_TRAINING_AI_ON_CHILDREN,
    THEME_VOICE_RECORDING,
    THEMES,
    is_amira_specific,
    match_themes,
)

# --- Structural guard --------------------------------------------------------


def test_theme_set_is_exactly_what_we_expect():
    """Names the set rather than counting it, so adding a theme fails with the
    name that changed instead of an opaque count mismatch."""
    assert set(THEMES) == {
        # The five narratives Angela named, written in PARENT register.
        THEME_VOICE_RECORDING,
        THEME_TRAINING_AI_ON_CHILDREN,
        THEME_IS_A_CHATBOT,
        THEME_PRIVACY_SURVEILLANCE,
        THEME_SCREEN_TIME_HARM,
        # Written in JOURNALIST register — the act of objecting, whatever the
        # grievance. This is what answers "where are the outcries growing".
        THEME_PARENT_OBJECTION,
    }


def test_every_theme_has_anchors():
    for theme, anchors in THEMES.items():
        assert len(anchors) >= 3, f"{theme!r} has too few anchors to be useful"


def test_all_anchors_are_multi_word():
    """Every anchor must be multi-word or a genuinely distinctive single
    token (the rule ``topic_config`` documents: a bare "ai" or "voice"
    substring-matches inside ordinary words and floods the result). This
    module takes the stricter of the two options and never uses a bare
    single-token anchor at all, so this assertion is unconditional — a
    future edit that adds one fails HERE, structurally, rather than quietly
    degrading precision.
    """
    for theme, anchors in THEMES.items():
        for anchor in anchors:
            assert " " in anchor, f"theme {theme!r} anchor {anchor!r} is not multi-word"


def test_no_duplicate_anchors_within_a_theme():
    for theme, anchors in THEMES.items():
        assert len(anchors) == len(set(anchors)), f"{theme!r} has duplicate anchors"


# --- voice_recording ----------------------------------------------------------


def test_voice_recording_matches_several_phrasings():
    texts = [
        "Parents are furious the app is recording children's voices every night.",
        "A local news story raised concerns about voice recordings of students "
        "made during reading sessions.",
        "The district confirmed the platform is collecting voice data from "
        "kindergartners without a clear consent form.",
        "She said the company is harvesting voice data and called it creepy.",
    ]
    for t in texts:
        assert THEME_VOICE_RECORDING in match_themes(t), t


def test_voice_recording_false_positive_school_choir():
    # From the brief's own false-positive list.
    t = "The school choir recorded their voices for the winter concert fundraiser."
    assert THEME_VOICE_RECORDING not in match_themes(t)


def test_voice_recording_false_positive_generic_audio_mention():
    t = "The assembly's audio system was upgraded so every classroom can be heard clearly."
    assert THEME_VOICE_RECORDING not in match_themes(t)


# --- training_ai_on_children ---------------------------------------------------


def test_training_ai_on_children_matches_several_phrasings():
    texts = [
        "The lawsuit alleges the company is training AI on children without parental knowledge.",
        "Critics say kids are training the AI every time they use the reading app.",
        "The complaint claims student data trains the model used across the whole platform.",
        "A blog post argued the company is using children as training data for a for-profit product.",
    ]
    for t in texts:
        assert THEME_TRAINING_AI_ON_CHILDREN in match_themes(t), t


def test_training_ai_false_positive_ai_club():
    # From the brief's own false-positive list.
    t = "Training for the AI club starts next week and meets every Thursday after school."
    assert THEME_TRAINING_AI_ON_CHILDREN not in match_themes(t)


def test_training_ai_false_positive_teacher_pd():
    t = "Teachers completed a training session on using AI tools to plan lessons."
    assert THEME_TRAINING_AI_ON_CHILDREN not in match_themes(t)


# --- is_a_chatbot ---------------------------------------------------------


def test_is_a_chatbot_matches_several_phrasings():
    texts = [
        "One parent dismissed it outright: 'it's just a chatbot, not a real teacher.'",
        "The op-ed called the product a glorified chatbot dressed up as a reading tutor.",
        "Some parents worry about replacing teachers with a chatbot instead of hiring more staff.",
        "The headline read: is this just ChatGPT for kids wearing a school-friendly skin?",
    ]
    for t in texts:
        assert THEME_IS_A_CHATBOT in match_themes(t), t


def test_is_a_chatbot_false_positive_district_website_chatbot():
    # From the brief's own false-positive list — the whole reason bare
    # "chatbot" is never used as an anchor.
    t = "The chatbot on the district website answers enrollment questions for new families."
    assert THEME_IS_A_CHATBOT not in match_themes(t)


def test_is_a_chatbot_false_positive_customer_service_chatbot():
    t = "The school's IT helpdesk added a chatbot to handle password reset requests."
    assert THEME_IS_A_CHATBOT not in match_themes(t)


# --- privacy_surveillance ---------------------------------------------------


def test_privacy_surveillance_matches_several_phrasings():
    texts = [
        "The board meeting turned tense after a parent raised data privacy concerns.",
        "Advocates say the platform amounts to surveillance of students during class time.",
        "The FAQ was updated after questions about who sees my child's data once it's uploaded.",
        "A watchdog group alleged the company is selling student data to third-party advertisers.",
        "The filing claims the vendor is in violation of COPPA.",
    ]
    for t in texts:
        assert THEME_PRIVACY_SURVEILLANCE in match_themes(t), t


def test_privacy_surveillance_false_positive_routine_policy_notice():
    t = "The district emailed families its annual privacy policy notice, as required each fall."
    assert THEME_PRIVACY_SURVEILLANCE not in match_themes(t)


def test_privacy_surveillance_false_positive_security_camera_unrelated():
    t = "New security cameras were installed at the main entrance over the summer."
    assert THEME_PRIVACY_SURVEILLANCE not in match_themes(t)


# --- screen_time_harm ---------------------------------------------------


def test_screen_time_harm_matches_several_phrasings():
    texts = [
        "Parents complained their kids are getting too much screen time from the new program.",
        "The letter to the board warned of screen time overload in early elementary classrooms.",
        "One commenter said the tool is contributing to screen addiction in six-year-olds.",
        "A pediatrician quoted in the piece worried about digital eye strain from daily use.",
    ]
    for t in texts:
        assert THEME_SCREEN_TIME_HARM in match_themes(t), t


def test_screen_time_harm_false_positive_neutral_screen_time_mention():
    # Neutral, non-harm-framed screen-time language must not trip the harm theme
    # (Screen-Time Watch's own topic gate is the right home for neutral policy
    # language; this theme is deliberately narrower).
    t = "Kids get a bit more screen time at home during summer break, the survey found."
    assert THEME_SCREEN_TIME_HARM not in match_themes(t)


def test_screen_time_harm_false_positive_literal_screen_hardware():
    t = "The library installed new touch screens for the checkout kiosks."
    assert THEME_SCREEN_TIME_HARM not in match_themes(t)


# --- apostrophe normalization (curly vs straight) --------------------------


def test_curly_apostrophe_still_matches_possessive_anchor():
    t = "The forum thread was about recording children’s voices during story time."
    assert THEME_VOICE_RECORDING in match_themes(t)


# --- multiple themes in one item --------------------------------------------


def test_text_can_carry_more_than_one_theme():
    t = (
        "The article combined two complaints: the app is training AI on children, "
        "and parents say it's just a chatbot pretending to teach reading."
    )
    themes = match_themes(t)
    assert THEME_TRAINING_AI_ON_CHILDREN in themes
    assert THEME_IS_A_CHATBOT in themes


def test_unrelated_text_matches_no_theme():
    t = "The football team won its homecoming game on Friday night in overtime."
    assert match_themes(t) == set()


# --- is_amira_specific: separable from match_themes -------------------------


def test_is_amira_specific_false_for_generic_national_narrative():
    """The brief's central example: this is a national conversation, not an
    Amira signal, until a brand or a district/state we care about is named."""
    t = "Across the country, parents are increasingly worried about children being used to train AI."
    assert THEME_TRAINING_AI_ON_CHILDREN in match_themes(t)
    assert is_amira_specific(t) is False


def test_is_amira_specific_true_when_amira_named():
    t = "Parents in the district say Amira is training AI on children without telling them."
    assert THEME_TRAINING_AI_ON_CHILDREN in match_themes(t)
    assert is_amira_specific(t) is True


def test_is_amira_specific_true_for_named_competitor():
    t = "The board heard complaints that iReady is just a chatbot pretending to be a teacher."
    assert THEME_IS_A_CHATBOT in match_themes(t)
    assert is_amira_specific(t) is True


def test_theme_without_brand_and_theme_with_brand_diverge():
    """Same theme, same wording shape, only the brand mention differs — the
    two functions must not be collapsed into one boolean."""
    generic = "Some parents say the reading app is just a chatbot, not a real teacher."
    branded = "Some parents say Amira is just a chatbot, not a real teacher."

    assert THEME_IS_A_CHATBOT in match_themes(generic)
    assert THEME_IS_A_CHATBOT in match_themes(branded)

    assert is_amira_specific(generic) is False
    assert is_amira_specific(branded) is True


def test_is_amira_specific_independent_of_theme_presence():
    """A brand mention with NO narrative theme still reads as Amira-specific;
    is_amira_specific never depends on match_themes internally."""
    t = "Amira Learning announced a new partnership with a regional library system."
    assert match_themes(t) == set()
    assert is_amira_specific(t) is True


def test_is_amira_specific_false_for_unrelated_text():
    t = "The football team won its homecoming game on Friday night in overtime."
    assert is_amira_specific(t) is False


# ── verb-form recall (added 2026-08-20 after a live gap) ─────────────────────
#
# Every original voice_recording anchor used the gerund ("recording children's
# voices"), but real complaints are written in the present or past tense. The
# most likely real phrasing of the theme Angela named FIRST did not match, while
# the false-positive guard for "the school choir recorded their voices" passed —
# the guard had been tuned against a phrasing that also rejected true positives.


@pytest.mark.parametrize(
    "text",
    [
        "Parents say the app records their children's voices without consent",
        "The district recorded children's voices and stored the audio",
        "A vendor that records student voices should need opt-in",
    ],
)
def test_voice_recording_matches_present_and_past_tense(text: str) -> None:
    assert THEME_VOICE_RECORDING in match_themes(text)


@pytest.mark.parametrize(
    "text",
    [
        "The school choir recorded their voices for the winter concert",
        "Students recorded their voices for a Spanish language project",
        "He recorded a voice memo about the meeting",
    ],
)
def test_verb_variants_did_not_loosen_the_guard(text: str) -> None:
    """The variants pair a record-verb with children's/student voices, so
    ordinary 'recorded their voices' prose must still not match."""
    assert THEME_VOICE_RECORDING not in match_themes(text)


@pytest.mark.parametrize(
    "text",
    [
        "Our district uses Amira and it records my kid's voice",
        "The app recorded my son's voice without asking",
        "It records my daughter's voice every session",
    ],
)
def test_voice_recording_matches_a_parents_own_phrasing(text: str) -> None:
    """First-person possessive is how a PARENT writes it, which is the single
    most likely form this theme takes on Reddit. Found by composing the Reddit
    normalizer with this matcher, not by reading the anchor list."""
    assert THEME_VOICE_RECORDING in match_themes(text)


# ── journalist register vs parent register ───────────────────────────────────
#
# Every other theme is written the way a PARENT complains. News is written the
# way a REPORTER summarises. A live Georgia sweep on 2026-08-20 returned
# "Schools, parents balk at AI testing for kindergarten students" — the single
# most on-point result — and it matched NOTHING. parent_objection closes that,
# and detects a flashpoint even when the underlying grievance is phrased in a
# way we have not anticipated.


@pytest.mark.parametrize(
    "headline",
    [
        "Schools, parents balk at AI testing for kindergarten students",
        "A $57,590 Robot Was Supposed to Transform Learning. Instead, It Triggered a Backlash",
        "Concerned parents packed the board meeting over the district's reading app",
        "Parents opt out of the AI reading assessment in growing numbers",
        "Parent outcry over student data prompts a district review",
    ],
)
def test_parent_objection_matches_real_headline_phrasing(headline: str) -> None:
    assert THEME_PARENT_OBJECTION in match_themes(headline)


@pytest.mark.parametrize(
    "headline",
    [
        "Georgia lawmakers push to regulate AI, algorithms",
        "Father of Georgia school shooter sentenced to 15 years in prison",
        "More than half of Georgia teachers use AI, CSRA educators weigh in",
        "District parents night is scheduled for Thursday",
    ],
)
def test_parent_objection_ignores_ordinary_and_tragedy_coverage(headline: str) -> None:
    """A 'Georgia schools parents' query pulls in unrelated tragedy coverage —
    three of fourteen results in the live sweep. Objection language must not
    fire on it."""
    assert THEME_PARENT_OBJECTION not in match_themes(headline)
