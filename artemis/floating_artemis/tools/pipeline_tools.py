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
    UNAVAILABLE,
    big_deals_without_contacts,
    loss_reason_availability,
    open_pipeline_by_stage,
    win_rate_by_size,
)

logger = logging.getLogger(__name__)

SALESFORCE_PIPELINE = "salesforce_pipeline"

_QUESTIONS = {
    "win_rate_by_size": win_rate_by_size,
    "open_pipeline_by_stage": open_pipeline_by_stage,
    "big_deals_without_contacts": big_deals_without_contacts,
    "loss_reason_availability": loss_reason_availability,
}


async def _salesforce_pipeline(inp: dict[str, Any], *, session_factory: Any = None) -> str:
    question = str(inp.get("question") or "").strip()
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

    return answer.render()


def register_pipeline_tools(registry: AuthorizedToolRegistry) -> None:
    registry.register(
        Tool(
            name=SALESFORCE_PIPELINE,
            description=(
                "Read-only Salesforce pipeline figures for a FIXED set of questions. "
                "Every answer states the filter that produced it and any known "
                "distortion in the data -- repeat both when quoting a number. "
                "Never state a pipeline figure you did not get from this tool in "
                "this turn. If it reports the data is unavailable, say so; that is "
                "NOT a report of zero. Questions: win_rate_by_size (win rate per "
                "deal-size band), open_pipeline_by_stage (current open pipeline), "
                "big_deals_without_contacts (large open deals with nobody attached), "
                "loss_reason_availability (whether Salesforce records WHY deals are "
                "lost -- it currently does not)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "enum": sorted(_QUESTIONS),
                        "description": "Which prepared question to run.",
                    }
                },
                "required": ["question"],
            },
        ),
        _salesforce_pipeline,
        layer=1,
    )
