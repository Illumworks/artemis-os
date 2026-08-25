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
    THEME_INSTITUTIONAL_REJECTION,
    THEME_IS_A_CHATBOT,
    THEME_PARENT_OBJECTION,
    THEME_PRIVACY_SURVEILLANCE,
    THEME_SCREEN_TIME_HARM,
    THEME_TRAINING_AI_ON_CHILDREN,
    THEME_VOICE_RECORDING,
    THEMES,
    has_tech_context,
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
        # INSTITUTIONAL register — districts, boards, teachers, legislators.
        # The commercially severe half: a parent complaint is sentiment, a
        # district vote is a lost contract.
        THEME_INSTITUTIONAL_REJECTION,
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


# ── objection must be PAIRED with tech context ───────────────────────────────
#
# From the first live national scan (2026-08-20): parent_objection on its own
# matched parents demanding answers over a backpack ban, a mascot name, a
# superintendent's leave and an FBI raid. Real parent anger, nothing to do with
# us. The act of objecting is only OUR signal when the object is ed-tech.


@pytest.mark.parametrize(
    "headline",
    [
        "Parents demand answers after Memphis middle school bans backpacks",
        "Parents petition to change high school mascot's naughty name",
        "Germantown parents demand transparency as superintendent is on leave",
        "Lawrence County parents push back on new absence policy",
        "Parents protest, call for action on bullying and mental health",
    ],
)
def test_real_parent_anger_without_tech_is_not_our_signal(headline: str) -> None:
    assert THEME_PARENT_OBJECTION in match_themes(headline)  # it IS objection
    assert not has_tech_context(headline)  # but not about us


@pytest.mark.parametrize(
    "headline",
    [
        "Schools, parents balk at AI testing for kindergarten students",
        "A.I.-Themed High School Is Put on Hold After Parental Backlash",
        "Elmira parents demand answers over teacher's AI misuse allegations",
        "A $57,590 Robot Was Supposed to Transform Learning. Instead, It Triggered a Backlash",
    ],
)
def test_objection_about_ed_tech_is_our_signal(headline: str) -> None:
    """All five are real headlines from the live scan."""
    assert THEME_PARENT_OBJECTION in match_themes(headline)
    assert has_tech_context(headline)


def test_tech_context_matches_real_headline_word_forms() -> None:
    """The real Arizona headline, verbatim from the live scan. It carries no
    objection language, so it is a tech-context signal only — and 'surveilling'
    must match, which the noun form 'surveillance' alone did not."""
    real = "Arizona schools are digitally surveilling students. Here's what parents need to know"
    assert has_tech_context(real)
    assert THEME_PARENT_OBJECTION not in match_themes(real)


# ── institutional_rejection ───────────────────────────────────────────────────
#
# Added 2026-08-24. A vendor-name sweep returned ~a dozen Amira-named stories
# that matched NO theme at all: every objection anchor made "parents" the
# grammatical subject, while the live New Mexico crisis is being driven by
# DISTRICTS, SCHOOL BOARDS, TEACHERS and LEGISLATORS. Each headline below is
# verbatim from that sweep.


@pytest.mark.parametrize(
    "headline",
    [
        "Santa Fe Public Schools rejects state-required AI program",
        "School Districts Push Back Against State-Required AI Reading Assessments",
        "Pinellas teachers raise concerns about AI reading program used in classrooms",
        "New Mexico Allows Schools to Opt Out of Controversial AI Tool",
        "Katy ISD restricts the use of AI in elementary school classrooms",
        "AI classroom robot plan put on hold in Salamanca",
        "Charlotte-Mecklenburg Schools shortens i-Ready contract over screen time",
    ],
)
def test_institutional_rejection_fires_on_real_coverage(headline: str) -> None:
    assert THEME_INSTITUTIONAL_REJECTION in match_themes(headline)
    assert has_tech_context(headline)


def test_institutional_rejection_is_distinct_from_parent_objection() -> None:
    """The two must not collapse: a parent complaint is sentiment, a district
    vote is a lost contract, and they escalate differently."""
    district = "Santa Fe Public Schools rejects state-required AI program"
    parent = "Parents are raising the alarm as schools roll out AI teachers without consent"
    assert match_themes(district) == {THEME_INSTITUTIONAL_REJECTION}
    assert match_themes(parent) == {THEME_PARENT_OBJECTION}


def test_a_story_can_carry_both_registers() -> None:
    both = "A.I.-Themed High School Is Put on Hold After Parental Backlash"
    assert match_themes(both) >= {THEME_INSTITUTIONAL_REJECTION, THEME_PARENT_OBJECTION}


@pytest.mark.parametrize(
    "benign",
    [
        # Ordinary district business that happens to use the same verbs.
        "School leaders gathered in Denver for the annual superintendents conference",
        "The board rejects the proposed boundary change after community input",
        "District drops its dress code requirement for high school seniors",
    ],
)
def test_institutional_rejection_without_tech_is_not_our_signal(benign: str) -> None:
    """Same pairing rule as parent_objection: the ACT of rejecting is only our
    signal when the thing rejected is ed-tech."""
    assert not has_tech_context(benign)


def test_parent_objection_matches_inverted_sentence_forms() -> None:
    """Coverage often makes parents the OBJECT, not the subject."""
    real = "Meet Amira, an AI reading tutor alarming some parents and school leaders in New Mexico"
    assert THEME_PARENT_OBJECTION in match_themes(real)
    assert is_amira_specific(real)


def test_hyphenated_initialism_is_tech_context() -> None:
    """KOAT styles it 'A-I'. Third house style after 'AI' and NYT's 'A.I.'."""
    assert has_tech_context(
        "Concerns about A-I in the classroom even though its mandated by NM PED"
    )


def test_bare_hyphen_substring_does_not_leak() -> None:
    """' a-i ' is space-bounded because 'a-i' sits inside ordinary words."""
    assert not has_tech_context("Our data-informed approach to reading instruction")
