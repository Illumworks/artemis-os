"""Callie's read-only window onto the Salesforce pipeline.

**A fixed menu, not a query language.** Every question is written in advance;
the agent picks one. Handing an LLM free SOQL is how it invents a filter, gets a
number, and reports it with the confidence of a real one.

**Fails closed, loudly.** If Salesforce cannot be reached the tool says the data
is unavailable and instructs the agent not to state any figure. It never returns
zero, and it never returns an empty table -- both read as findings.
"""

from __future__ import annotations

import logging
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry
from artemis.marketing.pipeline_intel import (
    NOT_COVERED,
    UNAVAILABLE,
    big_deals_without_contacts,
    closing_soon,
    deals_missing_contacts,
    loss_reasons,
    open_pipeline_by_stage,
    stalled_deals,
    win_rate_by_size,
)

logger = logging.getLogger(__name__)

SALESFORCE_PIPELINE = "salesforce_pipeline"

#: question name -> the coroutine that answers it. Typed loosely on purpose: the
#: handlers take different keyword defaults (days, limit, min_amount) and are all
#: called with just the client here.
_QUESTIONS: dict[str, Any] = {
    "win_rate_by_size": win_rate_by_size,
    "open_pipeline_by_stage": open_pipeline_by_stage,
    "stalled_deals": stalled_deals,
    "deals_missing_contacts": deals_missing_contacts,
    "closing_soon": closing_soon,
    "big_deals_without_contacts": big_deals_without_contacts,
    "loss_reasons": loss_reasons,
}

#: The escape hatch. An enum pushes the model to pick SOMETHING, so without this
#: a question outside the menu gets answered with the nearest available one -- a
#: real number under the wrong framing, which reads as an answer and is not.
NONE_OF_THESE = "none_of_these"


async def _salesforce_pipeline(inp: dict[str, Any], *, session_factory: Any = None) -> str:
    question = str(inp.get("question") or "").strip()
    if question == NONE_OF_THESE:
        return NOT_COVERED
    handler = _QUESTIONS.get(question)
    if handler is None:
        return (
            f"Unknown question {question!r}. Choose one of: "
            + ", ".join(sorted(_QUESTIONS))
            + ". Do not answer from memory -- if none of these fits, say the "
            "question cannot be answered with the tools available."
        )

    try:
        import artemis.db as _db
        from artemis.marketing.salesforce_suppression import _get_client

        factory = session_factory or _db.SessionLocal
        async with factory() as session:
            client = await _get_client(session)
        answer = await handler(client)
    except Exception:
        logger.warning("salesforce_pipeline: %s failed", question, exc_info=True)
        return UNAVAILABLE

    return str(answer.render())


def register_pipeline_tools(registry: AuthorizedToolRegistry) -> None:
    registry.register(
        Tool(
            name=SALESFORCE_PIPELINE,
            description=(
                "Read-only Salesforce pipeline figures for a FIXED set of questions. "
                "Map what the person actually asked onto the closest question ONLY "
                "if it genuinely answers them; otherwise pass 'none_of_these' and "
                "tell them what you can answer instead. Answering a different "
                "question than the one asked is worse than saying you cannot -- the "
                "number is real and the framing is wrong.\n"
                "Every answer states the filter that produced it and any known "
                "distortion in the data; repeat both when quoting a number. Never "
                "state a pipeline figure you did not get from this tool in this "
                "turn. If it reports the data is unavailable, say so -- that is NOT "
                "a report of zero.\n"
                "Questions, and the sorts of things they answer:\n"
                "- win_rate_by_size: 'how are we doing on big deals', 'what do we "
                "actually win', 'are we losing the enterprise stuff'\n"
                "- open_pipeline_by_stage: 'what's in the pipeline', 'where is "
                "everything sitting', 'how much is open'\n"
                "- stalled_deals: 'what's gone quiet', 'what's stuck', 'what has "
                "nobody touched'\n"
                "- deals_missing_contacts: 'which deals have nobody attached', "
                "'who are we even talking to'\n"
                "- closing_soon: 'what's closing this month', 'what lands soon'\n"
                "- big_deals_without_contacts: counts only, for the same question "
                "at $250k+\n"
                "- loss_reasons: 'why did we lose X', 'what are our loss reasons' "
                "-- reports Opportunity.Reason__c, the real field. Exclude 'Merged "
                "with another Opp', which is bookkeeping and not a loss\n"
                "- none_of_these: anything else, including forecasts, quota, "
                "commission, individual rep performance, or any question needing "
                "call or email activity"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "enum": [*sorted(_QUESTIONS), NONE_OF_THESE],
                        "description": (
                            "Which prepared question to run, or 'none_of_these' "
                            "when the person asked something the menu does not "
                            "answer."
                        ),
                    }
                },
                "required": ["question"],
            },
        ),
        _salesforce_pipeline,
        layer=1,
    )
