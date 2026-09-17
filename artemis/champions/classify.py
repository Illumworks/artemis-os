"""Classification for Champions items: summary, theme, and the two flags.

**The LLM judgment is the only product-issue flag.** Hannah's keyword list was
tested verbatim against all 390 educator posts in the community's history: the
58 multi-word phrases matched one post and it was a false positive; the word
stems flagged 20% of posts, almost entirely on the vocabulary of the field and
of Amira's own product (``struggl`` -> "struggling readers", ``challeng`` -> the
Amira Challenge, ``troubl`` -> "troubleshooting", ``bug`` -> a Champion whose
surname is Buggs). Her live digest flagged 45% against a true rate of 1-3%. The
stems are gone and are not coming back.

What survives from keywords is a deliberately different thing: a small
escalation tripwire for legal/safety/privacy language, routed to a person rather
than into the digest. Measured volume across the whole corpus: one hit. At that
rate recall matters more than precision and a human can eyeball every one.
"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass

from artemis.agent.client import CompletionRequest, ModelAdapter
from artemis.agent.types import Message, TextBlock
from artemis.providers.resolver import resolve_adapter

logger = logging.getLogger(__name__)

#: One of these, chosen by the LLM. A fixed list is what makes the Themes
#: rollup countable week over week; free-text themes drift and never aggregate.
THEMES: tuple[str, ...] = (
    "Scheduling & rostering",
    "Assessment & progress monitoring",
    "Reports & data",
    "Devices, audio & headphones",
    "Speech recognition & accents",
    "Student engagement & motivation",
    "Incentives, challenges & competitions",
    "Teacher adoption & PD",
    "Parent & family communication",
    "Praise & success story",
    "Product question",
    "Product problem",
    "Other",
)

#: Escalation tripwire. NOT the product-issue flag and not OR'd with it -- this
#: routes to a person. `publicly`, `legal` and `superintendent` are deliberately
#: absent: too common in innocent use.
ESCALATION_TERMS: tuple[str, ...] = (
    "privacy concern",
    "security concern",
    "data concern",
    "student safety",
    "school board",
    "formal complaint",
    "lawsuit",
    "breach",
    "refund",
    "unacceptable",
    "unsafe",
    "harmful",
    "fed up",
    "outrag",
    "furious",
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(body: str | None) -> str:
    """Vanilla bodies are HTML (or rich-text JSON); the LLM wants prose."""
    if not body:
        return ""
    text = _TAG_RE.sub(" ", body)
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def escalation_hits(text: str) -> list[str]:
    """Which tripwire terms appear. Substring match is intended: ``outrag``
    is a stem chosen to catch outrage/outraged/outrageous."""
    haystack = text.lower()
    return [term for term in ESCALATION_TERMS if term in haystack]


@dataclass(frozen=True)
class Classification:
    summary: str
    theme: str
    product_issue: bool
    adoption_friction: bool


_SYSTEM = f"""You classify posts from the Amira Learning Champions community, \
where US educators discuss using Amira (an AI reading tutor) with their students.

For the post you are given, return a JSON object with exactly these keys:

  "summary"            one sentence, max 25 words, plain past tense, no preamble.
                       Keep whatever the poster is UNHAPPY or STUCK about -- that
                       is the part a reader acts on. A summary that records the
                       question and drops "nobody could tell me" has lost the
                       point of the post.
  "theme"              exactly one of: {" | ".join(THEMES)}
  "product_issue"      true if Amira itself misbehaved: broke, failed to load, lost
                       data, misheard a student, scored wrongly, or a feature
                       stopped working. false otherwise.
  "adoption_friction"  true if the difficulty is about engagement, buy-in,
                       motivation, scheduling, or teacher reluctance. This is real
                       and useful, but it is NOT a product fault.

The distinction between those two flags is the whole point:
IS THE PRODUCT DOING SOMETHING WRONG, OR IS A PERSON FINDING THE WORK HARD?

THE TWO FLAGS ARE INDEPENDENT, NOT ALTERNATIVES. A post can be both, and posts
about a product fault very often are, because a fault is usually reported
through the frustration it caused. Decide each flag on its own. Never set
adoption_friction INSTEAD of product_issue just because the post is mostly about
how people felt.

Amira listens to children read aloud. So when a post says Amira did not hear a
student, did not pick up their voice, misheard them, would not respond, marked a
correctly-read word wrong, or could not cope with an accent, THAT IS
product_issue = true. It stays true when the teacher is asking for workarounds
rather than complaining, when the post also describes upset students, and when
the teacher blames the microphone or the headphones -- those are reports of the
product failing at its core job, and they are the single most common real
problem in this community.

Amira also fails when it behaves INCONSISTENTLY or inexplicably. A report that
the same assessment was given differently to different students, that a score
cannot be accounted for, that data changed between years, or that nobody at
Amira could explain what the product did, is product_issue = true. It is phrased
as a question far more often than as a complaint, and the question is the
complaint.

Set adoption_friction = true when the difficulty is about getting students or
teachers to engage, buy in, stay motivated, or fit sessions into the schedule,
and the product itself did what it was meant to do.

What is NOT a product issue: "struggling readers", "reading difficulty" and
"challenges with reading" are this field's ordinary vocabulary and describe
students, not faults. The Amira Challenge is a product FEATURE. Praise, a
shared tip, a classroom photo, or a question about how something works is not a
fault. Roughly 1-3% of posts describe a real product problem, so most posts
should have both flags false -- but never suppress a genuine fault to hit that
rate.

Return only the JSON object. No markdown fence, no commentary."""


def _coerce(raw: str) -> Classification | None:
    """Parse the model's reply, tolerating a fence but not a wrong shape."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    summary = str(data.get("summary") or "").strip()
    theme = str(data.get("theme") or "").strip()
    if not summary:
        return None
    # An off-vocabulary theme is the failure that quietly destroys the rollup,
    # so it is corrected to "Other" rather than stored as the model wrote it.
    if theme not in THEMES:
        logger.info("champions: off-vocabulary theme %r -> Other", theme)
        theme = "Other"
    return Classification(
        summary=summary,
        theme=theme,
        product_issue=bool(data.get("product_issue")),
        adoption_friction=bool(data.get("adoption_friction")),
    )


async def classify_item(
    *,
    title: str | None,
    body: str | None,
    item_type: str,
    adapter: ModelAdapter | None = None,
    model: str | None = None,
) -> Classification | None:
    """Classify one item. Returns None when the model's reply is unusable.

    None is deliberate: a row left unclassified is retried on the next run,
    whereas a fabricated default would be stored as though it were a judgment.
    """
    text = strip_html(body)
    if not (title or text):
        return None

    resolved = adapter if adapter is not None else resolve_adapter("claude-code")
    prompt = f"Type: {item_type}\n"
    if title:
        prompt += f"Title: {title}\n"
    prompt += f"Body: {text[:6000]}"

    response = await resolved.complete(
        CompletionRequest(
            messages=[Message(role="user", content=[TextBlock(text=prompt)])],
            system=_SYSTEM,
            model=model,
            max_tokens=1024,
        )
    )
    reply = "".join(
        block.text for block in response.message.content if isinstance(block, TextBlock)
    )
    return _coerce(reply)
