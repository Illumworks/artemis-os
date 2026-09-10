"""What KIND of concern a district raised — a category, never a word they said.

Carolyn asked in #market-signals on 2026-09-10 whether we could say a district's
concerns were about rostering, or parents, or training. Gong's 26 trackers are all
sales-process and cannot answer it, and getting new trackers configured would
repeat a two-month wait nobody will accept again. So we derive the category
ourselves.

**This is the only transcript read in the codebase, and it is built so that it
cannot become anything else.**

`GongMetadataClient` still has no transcript method and a test still asserts the
string is absent from it. Transcript access lives here, in one module, so the
audit surface is one file rather than a promise spread across a class everything
uses.

Four structural constraints, each with a test, because rule 4 has to be a gate
rather than an intention:

1. **The output is an enum.** `classify_call` returns members of
   `CONCERN_CATEGORIES` and nothing else. A model that emits a sentence has that
   sentence discarded, because there is no field for it to travel in. A quote
   cannot come out of a function whose return type is a fixed set of nine words.

2. **It runs locally or it does not run.** `_require_local_adapter` raises unless
   the resolved adapter is LM Studio. District conversations do not leave this
   network — the people in them consented to a call with a vendor, not to their
   words being posted to a third-party API. This is also why no cascade or
   fallback is wired: falling back to Claude on a local outage would send the
   transcript out, which is the one failure mode that must not be automatic.

3. **The text is never returned, logged, or persisted.** It exists as a local
   variable for the length of one call and is discarded. Nothing in this module
   writes text to the database; the only thing it produces is counts.

4. **Chunks are sized to the context the server actually has.** LM Studio reports
   `loaded_context_length` separately from `max_context_length`, so this asks
   rather than guesses: a model held at 131,072 takes a whole transcript in one
   call, and one evicted and reloaded just-in-time at its 4,096 default gets
   small pieces. Guessing either way is wrong half the time — assume large and a
   JIT reload silently truncates the input, assume small and every call is split
   needlessly, losing the cross-chunk context that makes the category obvious.

The output is the same shape as everything else here: counts of what calls touched
on. "Rowland raised rostering on 4 of 9 calls" is a fact about a district. It is
not, and cannot become, a fact about what anyone said.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

logger = logging.getLogger(__name__)

#: The complete set of things this can ever say. Adding a category is a
#: deliberate edit here; the model cannot invent one.
ConcernCategory = Literal[
    "rostering",
    "training",
    "implementation",
    "product_functionality",
    "reporting",
    "student_engagement",
    "parent",
    "pricing",
    "support",
    "other",
]

CONCERN_CATEGORIES: frozenset[str] = frozenset(get_args(ConcernCategory))

#: What each category means, given to the model and to anyone reading a result.
#: Deliberately about the SUBJECT of a concern, never its severity or its wording.
_CATEGORY_GUIDE = """\
rostering — getting students, classes, teachers or sections into the system; \
SIS sync, class lists, enrolment data, accounts not appearing
training — teacher professional development, onboarding, knowing how to use it
implementation — rollout, scheduling, devices, headphones, logistics, timelines
product_functionality — how the software itself behaves; features, bugs, gaps
reporting — data, dashboards, exports, evidence of progress, what admins can see
student_engagement — whether students use it, stick with it, or find it hard
parent — parent questions, pushback, communication home, consent, screen-time worries
pricing — cost, budget, renewal terms, contract value
support — responsiveness, escalations, getting help when something breaks
other — a concern that is genuinely none of the above\
"""

#: Fallback chunk size, used only when the server will not say what context it
#: has. Roughly 1,500 tokens, which is safe inside a 4,096-token default.
_FALLBACK_CHUNK_CHARS = 6000

#: Share of the context window given to transcript text. The rest holds the
#: instructions, the category guide, and the model's own reasoning block — which
#: on a reasoning model is not a rounding error.
_TEXT_SHARE_OF_CONTEXT = 0.5

#: Characters per token, roughly, for English prose.
_CHARS_PER_TOKEN = 4

#: A cap on how much of one call is examined. A 35-minute call is ~37,800
#: characters; beyond this the marginal chunk rarely changes the category set and
#: the cost is linear.
_MAX_CHARS_PER_CALL = 60_000


@dataclass(frozen=True)
class ConcernProfile:
    """Which kinds of concern a district raised, and on how many calls.

    Counts only. There is no field here that could hold a sentence.
    """

    account_name: str
    calls_examined: int = 0
    #: category -> number of CALLS on which it appeared (not chunks, not mentions)
    counts: dict[str, int] = field(default_factory=dict)
    #: True when the classifier could not be run at all, so an empty result is
    #: distinguishable from "no concerns found".
    unavailable: bool = False

    def describe(self) -> str:
        if self.unavailable:
            return (
                f"{self.account_name}: concern categories could not be derived "
                "(the local classifier was unavailable). UNKNOWN, not 'no concerns'."
            )
        if not self.calls_examined:
            return f"{self.account_name}: no calls available to examine."
        if not self.counts:
            return (
                f"{self.account_name}: {self.calls_examined} call(s) examined, no concern "
                "category identified. That is not the same as a happy district — it means "
                "nothing in these calls matched a category."
            )
        ranked = sorted(self.counts.items(), key=lambda kv: -kv[1])
        top, top_n = ranked[0]
        rest = ", ".join(f"{cat} {n}" for cat, n in ranked[1:5])
        # Lead with the dominant category and put the rest behind it, because the
        # NUMBER of categories is not a severity measure and reads like one in a
        # list. Measured 2026-09-10: a district the brief calls "sounding
        # positive" returns four categories and a concern-heavy one returns six,
        # so a count separates them barely. What separates them clearly is which
        # category dominates and at what rate -- Pinellas rostering on 3 of 3,
        # Madera product_functionality on 3 of 3.
        line = (
            f"{self.account_name}: mainly {top.replace('_', ' ')} — raised on {top_n} of "
            f"{self.calls_examined} call(s) examined"
        )
        if rest:
            line += f". Also present: {rest}"
        return (
            line + ". Categories only; this says what a concern was ABOUT, never what "
            "anyone said about it. A category appearing does not rank how serious it was."
        )


def _require_local_adapter(adapter: Any) -> None:
    """Refuse to classify anywhere but the local box.

    Not a configuration option and not a preference. A transcript sent to a
    hosted API is a district's conversation leaving the building, and the people
    on the call consented to talking to a vendor, not to that. Raising here rather
    than falling back is deliberate: a fallback would make the unsafe path the
    automatic response to a local outage.
    """
    from artemis.providers.lm_studio.adapter import LMStudioAdapter

    if not isinstance(adapter, LMStudioAdapter):
        raise RuntimeError(
            "Gong transcripts may only be classified by the local model. "
            f"Refusing to send call content to {type(adapter).__name__}."
        )


async def _chunk_chars() -> int:
    """How much transcript fits in one prompt, asked rather than assumed.

    A model held resident at 131,072 tokens can take a whole call at once, which
    is both faster and better — a category is easier to see with the whole
    conversation in view than through a 6,000-character window. A model that LM
    Studio loaded just-in-time gets its DEFAULT context, which for these is 4,096,
    and the same prompt would be silently truncated.
    """
    import httpx

    from artemis.config import settings

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{settings.lm_studio_base_url.rstrip('/')}/api/v0/models")
        resp.raise_for_status()
        # Only models that could serve this completion. The embedding model is
        # loaded at 2,048 and taking the minimum across everything let it decide
        # the transcript budget for a 131,072-token chat model that will actually
        # do the work.
        loaded = [
            int(m.get("loaded_context_length") or 0)
            for m in resp.json().get("data", [])
            if m.get("state") == "loaded"
            and m.get("loaded_context_length")
            and "embedding" not in str(m.get("id", "")).lower()
            and str(m.get("type", "")).lower() not in ("embeddings", "embedding")
        ]
    except Exception:
        logger.warning("concern classifier: could not read the loaded context length")
        return _FALLBACK_CHUNK_CHARS

    if not loaded:
        return _FALLBACK_CHUNK_CHARS
    # The smallest CHAT window still loaded, because we do not control which of
    # them serves the request.
    budget = int(min(loaded) * _TEXT_SHARE_OF_CONTEXT * _CHARS_PER_TOKEN)
    return max(_FALLBACK_CHUNK_CHARS, budget)


def _chunks(text: str, chunk_chars: int = _FALLBACK_CHUNK_CHARS) -> list[str]:
    """Split on paragraph-ish boundaries, then hard-split anything still too long."""
    capped = text[:_MAX_CHARS_PER_CALL]
    out: list[str] = []
    current = ""
    for para in capped.split("\n"):
        if len(current) + len(para) + 1 > chunk_chars:
            if current.strip():
                out.append(current)
            current = ""
        if len(para) > chunk_chars:
            for i in range(0, len(para), chunk_chars):
                out.append(para[i : i + chunk_chars])
            continue
        current = f"{current}\n{para}" if current else para
    if current.strip():
        out.append(current)
    return out


def _parse_categories(raw: str) -> set[str]:
    """Keep only real category names. Everything else the model said is dropped.

    This is the gate, not a formatting nicety: the model's prose, its reasoning,
    and any sentence it decided to quote all fail to match a member of the enum
    and are discarded here. There is no branch that lets unrecognised text
    through.
    """
    found: set[str] = set()
    lowered = raw.lower()
    for category in CONCERN_CATEGORIES:
        if category in lowered:
            found.add(category)
    return found


async def classify_call(transcript_text: str) -> set[str]:
    """Categories present in one call's transcript. Local model only.

    ``transcript_text`` is read and discarded. It is never returned, logged or
    written anywhere by this function.
    """
    from artemis.agent.client import CompletionRequest
    from artemis.agent.types import Message, TextBlock
    from artemis.providers.lm_studio.adapter import LMStudioAdapter

    adapter = LMStudioAdapter()
    _require_local_adapter(adapter)

    found: set[str] = set()
    size = await _chunk_chars()
    for chunk in _chunks(transcript_text, size):
        # The bar is the whole design. Without it this listed every TOPIC the call
        # touched, and a district the brief calls "sounding positive" came back
        # with seven of nine concern categories -- more than one flagged as
        # concern-heavy. A customer-success call discusses rostering, training and
        # implementation as a matter of course; none of that is a concern.
        prompt = (
            "Below is part of a transcript of a call between a vendor (Amira) and a "
            "school district.\n\n"
            "Identify only the topics where the DISTRICT expressed a PROBLEM: "
            "something not working, a difficulty, a complaint, an unmet need, a "
            "risk they are worried about, or a blocker.\n\n"
            "Do NOT include a topic merely because it was discussed, planned, "
            "explained, demonstrated, or agreed. Routine coordination is not a "
            "concern. Enthusiasm is not a concern. A question is not a concern "
            "unless it carries a difficulty.\n\n"
            f"Categories:\n{_CATEGORY_GUIDE}\n\n"
            "Most calls will match ONE or TWO categories, and many will match "
            "none. Reply with ONLY the matching category names, comma separated. "
            "If the district raised no problem at all, reply with exactly: none\n"
            "Do not quote or summarise anything that was said.\n\n"
            f"---\n{chunk}\n---"
        )
        try:
            response = await adapter.complete(
                CompletionRequest(
                    messages=[Message(role="user", content=[TextBlock(text=prompt)])],
                    max_tokens=8192,
                )
            )
        except Exception:
            # No transcript content in this log line, deliberately.
            logger.warning("concern classifier: a chunk failed to classify", exc_info=True)
            continue
        raw = "".join(getattr(b, "text", "") for b in response.message.content)
        found |= _parse_categories(raw)
    return found


async def _fetch_transcripts(call_ids: list[str]) -> dict[str, str]:
    """The only transcript read in this codebase.

    Deliberately a module-level function rather than a method on
    `GongMetadataClient`: that class's identity is "cannot fetch a transcript" and
    a test asserts the endpoint string is absent from it. Keeping this here means
    one file to audit rather than a capability added to the client every feature
    already holds.

    Returns callId -> plain text, in memory. The caller must not persist it.
    """
    import base64

    import httpx

    from artemis.config import settings

    if not call_ids:
        return {}
    token = base64.b64encode(
        f"{settings.gong_access_key}:{settings.gong_access_key_secret}".encode()
    ).decode()
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://api.gong.io/v2/calls/transcript",
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
            json={"filter": {"callIds": call_ids}},
        )
    resp.raise_for_status()

    out: dict[str, str] = {}
    for entry in resp.json().get("callTranscripts", []) or []:
        call_id = str(entry.get("callId") or "")
        sentences: list[str] = []
        for segment in entry.get("transcript", []) or []:
            for sentence in segment.get("sentences", []) or []:
                text = str(sentence.get("text") or "").strip()
                if text:
                    sentences.append(text)
        if call_id and sentences:
            out[call_id] = "\n".join(sentences)
    return out


async def classify_account(
    account_name: str, *, days: int = 120, max_calls: int = 8
) -> ConcernProfile:
    """Which kinds of concern a district raised, across its recent calls.

    `max_calls` is a real cost control, not a formality: each call is chunked and
    every chunk is a local inference of tens of seconds. Eight calls is enough to
    characterise a district and bounded enough to run in a background job.
    """
    from artemis.config import settings
    from artemis.integrations.gong.client import GongMetadataClient

    if not settings.gong_access_key or not settings.gong_access_key_secret:
        return ConcernProfile(account_name=account_name, unavailable=True)

    try:
        client = GongMetadataClient(settings.gong_access_key, settings.gong_access_key_secret)
        calls = await client.recent_calls_for_account(account_name, days=days, limit=max_calls)
        # `recent_calls_for_account` already drops these, and this checks again
        # anyway. Reading the transcript of a call somebody marked private is the
        # worst thing this module could do, and it is not a place to rely on a
        # caller having done the right thing.
        calls = [c for c in calls if not c.is_private]
    except Exception:
        logger.warning("concern classifier: could not list calls for %r", account_name)
        return ConcernProfile(account_name=account_name, unavailable=True)

    if not calls:
        return ConcernProfile(account_name=account_name, calls_examined=0)

    call_ids = [c.call_id for c in calls if c.call_id][:max_calls]
    try:
        transcripts = await _fetch_transcripts(call_ids)
    except Exception:
        logger.warning("concern classifier: transcript fetch failed for %r", account_name)
        return ConcernProfile(account_name=account_name, unavailable=True)

    counts: dict[str, int] = {}
    examined = 0
    for text in transcripts.values():
        if not text:
            continue
        examined += 1
        for category in await classify_call(text):
            counts[category] = counts.get(category, 0) + 1
        # `text` goes out of scope here and is never written anywhere.

    return ConcernProfile(account_name=account_name, calls_examined=examined, counts=counts)
