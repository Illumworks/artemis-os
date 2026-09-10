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

4. **Transcripts are chunked and classified in pieces.** A 35-minute call is
   ~9,450 tokens and LM Studio's just-in-time loading gives a model its DEFAULT
   4,096-token context, not its maximum. Chunking is correct under either
   configuration and keeps any single prompt small.

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
pricing — cost, budget, renewal terms, contract value
support — responsiveness, escalations, getting help when something breaks
other — a concern that is genuinely none of the above\
"""

#: Characters per chunk. Roughly 1,500 tokens, comfortably inside a 4,096-token
#: default context alongside the instructions and the model's own reasoning.
_CHUNK_CHARS = 6000

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
        parts = ", ".join(f"{cat} on {n} of {self.calls_examined}" for cat, n in ranked)
        return (
            f"{self.account_name}: concern categories across {self.calls_examined} call(s) — "
            f"{parts}. Categories only; this says what a concern was ABOUT, never what "
            "anyone said about it."
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


def _chunks(text: str) -> list[str]:
    """Split on paragraph-ish boundaries, then hard-split anything still too long."""
    capped = text[:_MAX_CHARS_PER_CALL]
    out: list[str] = []
    current = ""
    for para in capped.split("\n"):
        if len(current) + len(para) + 1 > _CHUNK_CHARS:
            if current.strip():
                out.append(current)
            current = ""
        if len(para) > _CHUNK_CHARS:
            for i in range(0, len(para), _CHUNK_CHARS):
                out.append(para[i : i + _CHUNK_CHARS])
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
    for chunk in _chunks(transcript_text):
        prompt = (
            "Below is part of a transcript of a call between a vendor and a school "
            "district. Identify which KINDS of concern the district raised.\n\n"
            f"Categories:\n{_CATEGORY_GUIDE}\n\n"
            "Reply with ONLY the matching category names, comma separated. If none "
            "apply, reply with: none\n"
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
