"""Pure narrative-theme matcher for the parent-sentiment / narrative watch.

Brief: ``briefs/parent-sentiment-watch.md``, Design §1. Angela's ask names
three narrative frames parents are using against Amira — voice recordings,
"children training AI", "it's just a chatbot" — and the brief adds two more
that the existing Screen-Time Watch work already treats as adjacent: general
data-privacy/surveillance unease, and the "screen time is harmful" framing.
Every source this watch adds later inherits this vocabulary unchanged.

THE PRECISION REQUIREMENT (read this before touching an anchor list):

    "Children being used to train AI" is a NATIONAL conversation. A theme
    match alone is a TOPIC signal — it says the narrative is present in the
    text, nothing more. Theme + brand, or theme + a district/state we care
    about, is an AMIRA signal.

``match_themes`` and ``is_amira_specific`` are therefore two independent
functions, not one merged boolean. A caller needs to be able to ask "how loud
is this narrative generally" and "is it pointed at us" separately — collapsing
them into a single "is this a threat" flag would make the report overstate
the threat, which in a live crisis is worse than under-reporting.

MATCHING STRATEGY — same as ``artemis.screentime.filters.topic_prescreen``:
plain, case-folded substring matching against a fixed anchor list per theme.
That is exactly why every anchor here is MULTI-WORD. ``topic_config`` already
documents the failure mode this avoids: a bare token like "ai" or "voice"
substring-matches inside ordinary words ("email", "voicemail", "available")
and floods the result with false positives. This module goes one step
further than ``topic_config`` (which allows a short list of hand-curated
single-token brand names): every anchor below contains a space, full stop —
see ``test_all_anchors_are_multi_word`` for the structural check that keeps
it that way.

PURE / no I/O. No provider calls, no DB, no network. Sources (§2), scoring
(§3), and delivery (§4) from the brief are separate, later work.
"""

from __future__ import annotations

from artemis.screentime.topic_config import DEFAULT_TOPIC_RULES

# --- Theme names -------------------------------------------------------------

THEME_VOICE_RECORDING = "voice_recording"
THEME_TRAINING_AI_ON_CHILDREN = "training_ai_on_children"
THEME_IS_A_CHATBOT = "is_a_chatbot"
THEME_PRIVACY_SURVEILLANCE = "privacy_surveillance"
THEME_SCREEN_TIME_HARM = "screen_time_harm"
THEME_PARENT_OBJECTION = "parent_objection"

# --- Anchors -------------------------------------------------------------
#
# Each theme is a tuple of multi-word phrases that genuinely express the
# narrative, gathered from several different real-world phrasings (a news
# headline, a Reddit comment, a parent's complaint) rather than one anchor
# pasted straight from the brief. Deliberately excluded: any phrasing that
# merely contains the theme's *topic words* without its *narrative framing*
# — see the false-positive-guard tests in test_themes.py, especially
# "the chatbot on the district website answers enrollment questions", which
# is why THEME_IS_A_CHATBOT never anchors on the bare word "chatbot".

THEMES: dict[str, tuple[str, ...]] = {
    # Angela: "voice recordings" — parents alleging the product records or
    # otherwise captures children's voices as data, without clear consent.
    THEME_VOICE_RECORDING: (
        "recording children's voices",
        "recording student voices",
        "recording my child's voice",
        "recording my kid's voice",
        "voice recordings of children",
        "voice recordings of students",
        "collecting voice data",
        "harvesting voice data",
        "voice biometric data",
        "capturing children's voices",
        "voice data without consent",
        # Verb-form coverage. Every anchor above uses the gerund ("recording"),
        # but real complaints are written in the present or past tense — "the
        # app records their children's voices", "it recorded my son's voice".
        # Missing those meant the most-likely real phrasing of the theme Angela
        # named FIRST did not match. Each variant still pairs a record-verb with
        # children's/student voices, so the false-positive guard that rejects
        # "the school choir recorded their voices" is unaffected: that phrase
        # has no children's/student qualifier.
        "records children's voices",
        "recorded children's voices",
        "record children's voices",
        "records student voices",
        "recorded student voices",
        "children's voices are recorded",
        "children's voices without consent",
        # Same verb-form gap, first-person possessive. Caught composing the
        # Reddit normalizer with this matcher: "it records my kid's voice" is
        # how a parent actually writes it, while only the gerund
        # ("recording my kid's voice") was covered. A parent's own phrasing is
        # the single most likely form this theme takes on Reddit.
        "records my child's voice",
        "recorded my child's voice",
        "records my kid's voice",
        "recorded my kid's voice",
        "records my son's voice",
        "recorded my son's voice",
        "records my daughter's voice",
        "recorded my daughter's voice",
    ),
    # Angela: "children being used to train AI" — kids' data, voices, or
    # schoolwork feeding model training, framed as extraction rather than
    # instruction.
    THEME_TRAINING_AI_ON_CHILDREN: (
        "training ai on children",
        "training ai on kids",
        "children training the ai",
        "kids are training the ai",
        "children being used to train ai",
        "kids being used to train ai",
        "student data trains the model",
        "children's data trains the ai",
        "using student data to train ai",
        "using children's data to train ai",
        "training data from children",
        "children as training data",
        "training the algorithm on student data",
        "harvesting student data to train ai",
    ),
    # Angela: "Amira is a chatbot" — the dismissive frame that the product is
    # not real instruction, or that AI is replacing teachers. Bare "chatbot"
    # is deliberately absent: it also appears in ordinary, unrelated coverage
    # of district chatbots (enrollment FAQs, IT helpdesks) that carries none
    # of this narrative.
    THEME_IS_A_CHATBOT: (
        "just a chatbot",
        "it's just a chatbot",
        "glorified chatbot",
        "nothing more than a chatbot",
        "chatbot pretending to be a teacher",
        "chatbot posing as a teacher",
        "replacing teachers with a chatbot",
        "ai chatbot replacing teachers",
        "chatgpt for kids",
        "ai posing as a teacher",
        "chatbot masquerading as a tutor",
    ),
    # Adjacent to the above two: general data-privacy / surveillance unease
    # (COPPA/FERPA, "who sees the data") rather than the voice- or
    # training-specific framings above.
    THEME_PRIVACY_SURVEILLANCE: (
        "student data privacy",
        "children's data privacy",
        "data privacy concerns",
        "student privacy violation",
        "children's privacy rights",
        "surveilling students",
        "surveillance of students",
        "who sees my child's data",
        "selling student data",
        "selling children's data",
        "student data breach",
        "coppa violation",
        "violation of coppa",
        "coppa compliance",
        "ferpa violation",
        "violation of ferpa",
        "ferpa concerns",
        "invasive data collection",
    ),
    # Already partly covered by Screen-Time Watch's own topic gate
    # (``artemis.screentime.topic_config``), which is tuned to catch neutral
    # POLICY language ("screen time", "device time"). This theme is
    # deliberately narrower and HARM-framed — parents complaining screen time
    # itself is damaging — so it does not fire on ordinary policy coverage.
    THEME_SCREEN_TIME_HARM: (
        "too much screen time",
        "excessive screen time",
        "screen time overload",
        "screen time is hurting",
        "harmful screen time",
        "screen addiction",
        "screen time concerns",
        "staring at screens all day",
        "replacing books with screens",
        "digital eye strain",
        "screen fatigue",
    ),
    # JOURNALIST REGISTER, not parent register. Added 2026-08-20 after a live
    # scan: every theme above is written the way a PARENT complains ("it records
    # my kid's voice"), but news is written the way a REPORTER summarises
    # ("Schools, parents balk at AI testing for kindergarten students"). That
    # real Georgia headline — the single most on-point result in the sweep —
    # matched nothing. Same narrative, different vocabulary.
    #
    # This theme is the ACT of objecting rather than any specific grievance,
    # which is what answers "where are these outcries growing": it detects a
    # flashpoint even when the underlying complaint is phrased in a way we have
    # not anticipated.
    THEME_PARENT_OBJECTION: (
        "parents balk at",
        "parents push back",
        "parents object to",
        "parents are objecting",
        "parents raise concerns",
        "parents raised concerns",
        "parent backlash",
        "parental backlash",
        "triggered a backlash",
        "sparked a backlash",
        "facing backlash",
        "parent outcry",
        "public outcry",
        "parents demand",
        "parents petition",
        "parents protest",
        "parents are protesting",
        "parents complain",
        "parents complained",
        "parents opt out",
        "parents opting out",
        "opt-out requests",
        "packed board meeting",
        "angry parents",
        "concerned parents",
    ),
}

# The brand/competitor vocabulary is owned by Screen-Time Watch
# (``artemis/screentime/topic_config.py``) — imported here, never duplicated,
# so there is one source of truth for "who counts as Amira or a competitor".
_BRAND_ANCHORS: tuple[str, ...] = tuple(
    str(term).lower() for term in DEFAULT_TOPIC_RULES.get("brand_any", ())
)


def _normalize(text: str) -> str:
    """Case-fold and collapse curly apostrophes to straight ones.

    Several anchors are possessive phrases ("children's voices", "my child's
    voice"). Scraped news/Reddit text mixes straight (') and typographic
    (’/‘) apostrophes; without this fold, a real-world hit using the
    typographic form would silently miss. No other normalization is applied —
    this stays a plain substring matcher, same strategy as
    ``topic_config``/``topic_prescreen``, on purpose.
    """
    return text.lower().replace("’", "'").replace("‘", "'")


def match_themes(text: str) -> set[str]:
    """Return the set of narrative-theme names whose anchors appear in *text*.

    Pure, deterministic, no I/O. This is a TOPIC signal only — see the module
    docstring's precision note. A hit here says the narrative is present;
    combine with ``is_amira_specific`` (or a caller's own district/state
    check) to decide whether it is actually pointed at Amira.
    """
    lower = _normalize(text)
    return {name for name, anchors in THEMES.items() if any(anchor in lower for anchor in anchors)}


def is_amira_specific(text: str) -> bool:
    """True iff *text* names Amira or a named competitor.

    Reuses Screen-Time Watch's ``brand_any`` list (one source of truth for
    vendor names) rather than duplicating it — read that file, do not edit
    it. Deliberately independent of ``match_themes``: see the module
    docstring's precision note. To decide "is this an Amira signal", a caller
    combines the two — theme match AND (brand match here OR a district/state
    the caller cares about) — rather than relying on a single merged boolean.
    """
    lower = _normalize(text)
    return any(anchor in lower for anchor in _BRAND_ANCHORS)
