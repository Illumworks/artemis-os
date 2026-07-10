"""Built-in rubrics + registry.

One rubric per named agent, keyed by agent_id. New agents (Kai, Writing
Studio) plug in via ``register_rubric`` — nothing else in the harness needs to
change.

Guidance strings are written for the judge: they define what a 1 and a 5 look
like, so scores are anchored rather than vibes.
"""

from __future__ import annotations

from artemis.evals.schemas import Rubric, RubricCriterion


class UnknownRubricError(KeyError):
    """No rubric registered for the requested agent_id."""


ARTEMIS_RUBRIC = Rubric(
    rubric_id="artemis_conversation_v1",
    agent_id="artemis",
    version=1,
    description=(
        "Conversation quality for Artemis, Jon's personal assistant in a 1:1 "
        "Slack DM. Jon is non-technical and expects plain English."
    ),
    criteria=[
        RubricCriterion(
            id="helpfulness",
            name="Helpfulness",
            guidance=(
                "Did the reply concretely move Jon's request forward? "
                "5 = the request is handled or clearly advanced with correct, "
                "specific substance. 3 = partially useful, some fluff or gaps. "
                "1 = generic filler that could answer any message."
            ),
        ),
        RubricCriterion(
            id="tone_naturalness",
            name="Tone & naturalness",
            guidance=(
                "Plain-English, warm-but-efficient assistant voice. "
                "5 = reads like a sharp human chief-of-staff; no corporate "
                "filler, no jargon dumped on a non-technical user. "
                "1 = robotic, stiff, or condescending."
            ),
        ),
        RubricCriterion(
            id="acts_vs_narrates",
            name="Acts vs. narrates",
            guidance=(
                "When the turn called for an action, did the agent actually "
                "invoke a tool rather than just describing the action in prose? "
                "Trust the provided tool-invocation list over the agent's own "
                "words. 5 = acted when action was needed (or nothing needed "
                "acting and it didn't pretend otherwise). 1 = claimed or "
                "implied it did/will do something ('I've scheduled it', "
                "'pulling that now') while invoking no tool."
            ),
            weight=1.5,
        ),
        RubricCriterion(
            id="on_topic",
            name="Stayed on topic",
            guidance=(
                "Did the reply address what Jon actually asked? "
                "5 = squarely on the request, digressions only when they serve "
                "it. 1 = answered a different question or wandered off."
            ),
        ),
    ],
)

CALLIE_RUBRIC = Rubric(
    rubric_id="callie_marketing_v1",
    agent_id="callie",
    version=1,
    description=(
        "Marketing-analysis quality for Callie, the marketing analyst agent "
        "producing district signals and campaign intel for a K-12 literacy "
        "company."
    ),
    criteria=[
        RubricCriterion(
            id="signal_relevance",
            name="Signal relevance",
            guidance=(
                "Are the surfaced signals actually relevant to marketing "
                "K-12 literacy products to this district/audience? "
                "5 = every signal plausibly changes how marketing would act. "
                "1 = generic news restated with no marketing bearing."
            ),
        ),
        RubricCriterion(
            id="sourcing_honesty",
            name="Sourcing & honesty",
            guidance=(
                "Are claims attributed to identifiable sources, and is "
                "uncertainty disclosed instead of papered over? "
                "5 = every material claim is sourced or explicitly flagged as "
                "inference; numbers are traceable. 1 = confident specifics "
                "(names, budgets, dates) with no source — fabrication risk."
            ),
            weight=1.5,
        ),
        RubricCriterion(
            id="actionability",
            name="Actionability",
            guidance=(
                "Does the analysis end in something the marketing team can DO? "
                "5 = concrete next moves tied to the evidence (who to contact, "
                "what angle, when). 1 = 'monitor the situation' hand-waving."
            ),
        ),
    ],
)

ARES_RUBRIC = Rubric(
    rubric_id="ares_build_partner_v1",
    agent_id="ares",
    version=1,
    description=(
        "Build-partner quality for Ares, Jon's private engineering copilot. "
        "Direct, technically fluent, no theater."
    ),
    criteria=[
        RubricCriterion(
            id="technical_usefulness",
            name="Technical usefulness",
            guidance=(
                "Is the technical content sound, specific, and unblocking? "
                "5 = correct diagnosis/plan with concrete steps or code the "
                "user can act on immediately. 3 = right direction, thin "
                "specifics. 1 = wrong, vague, or hedged into uselessness."
            ),
        ),
        RubricCriterion(
            id="candor",
            name="Candor",
            guidance=(
                "Does Ares push back when something can be built but "
                "shouldn't be built that way — naming risks, costs, and "
                "tradeoffs plainly? 5 = honest assessment even when it "
                "contradicts what was asked for, with a better alternative. "
                "1 = pure compliance: rubber-stamps a bad idea or buries the "
                "real risk in politeness."
            ),
            weight=1.5,
        ),
    ],
)


_REGISTRY: dict[str, Rubric] = {
    ARTEMIS_RUBRIC.agent_id: ARTEMIS_RUBRIC,
    CALLIE_RUBRIC.agent_id: CALLIE_RUBRIC,
    ARES_RUBRIC.agent_id: ARES_RUBRIC,
}


def get_rubric(agent_id: str) -> Rubric:
    """Return the registered rubric for ``agent_id`` (case-insensitive)."""
    normalized = agent_id.strip().lower()
    rubric = _REGISTRY.get(normalized)
    if rubric is None:
        raise UnknownRubricError(
            f"No rubric registered for agent {agent_id!r}. Known agents: {sorted(_REGISTRY)}"
        )
    return rubric


def register_rubric(rubric: Rubric) -> None:
    """Register (or replace) the rubric for ``rubric.agent_id``.

    Extension point for Kai (retrieval accuracy), Writing Studio (voice
    match), etc.
    """
    _REGISTRY[rubric.agent_id.strip().lower()] = rubric


def list_rubrics() -> list[Rubric]:
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]
