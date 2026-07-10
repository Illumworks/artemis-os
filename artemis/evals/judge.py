"""LLM-as-judge grader.

``grade_case(adapter, rubric, case)`` sends one judge prompt through the
provider-adapter layer and returns a ``CaseGrade``. Parsing is defensive
(code fences, surrounding prose, string scores, unknown/missing criteria) and
fail-safe: after one strict-format retry, an unparseable judge reply becomes a
CaseGrade with ``error`` set — the harness never crashes mid-run.

The judge model/provider is whatever ``ModelAdapter`` the caller resolves via
``artemis.providers`` (see harness.py); this module never constructs one.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from artemis.agent.client import CompletionRequest, ModelAdapter
from artemis.agent.types import Message, TextBlock
from artemis.evals.schemas import (
    SCORE_MAX,
    SCORE_MIN,
    CaseGrade,
    CriterionScore,
    EvalCase,
    Rubric,
)

logger = logging.getLogger(__name__)

_MAX_JUSTIFICATION_CHARS = 500
_MAX_COMMENT_CHARS = 800


class JudgeParseError(ValueError):
    """Judge reply could not be turned into criterion scores."""


# ── Prompt construction ──────────────────────────────────────────────────────

JUDGE_SYSTEM_PROMPT = (
    "You are a strict, consistent evaluator of AI-agent outputs. You grade "
    "one agent turn against a fixed rubric. You never grade generously to be "
    "nice; you anchor every score to the rubric's guidance. You reply with "
    "STRICT JSON only — no markdown fences, no prose before or after."
)


def build_judge_prompt(rubric: Rubric, case: EvalCase) -> str:
    """Render the full judge prompt for one case.

    NOTE: the JSON shape described here MUST stay in sync with
    ``parse_judge_output`` — the parser rejects criteria ids not in the rubric
    and records rubric ids the judge skipped.
    """
    transcript_lines = [f"[{turn.role}] {turn.text}" for turn in case.input_transcript]
    transcript = "\n".join(transcript_lines) or "(no prior context)"
    tool_calls = ", ".join(case.tool_calls) if case.tool_calls else "(none)"

    criteria_block = "\n".join(f'- id: "{c.id}" — {c.name}: {c.guidance}' for c in rubric.criteria)
    ids = ", ".join(f'"{c.id}"' for c in rubric.criteria)

    return (
        f"Agent under evaluation: {rubric.agent_id}\n"
        f"Rubric: {rubric.rubric_id} — {rubric.description}\n\n"
        f"## Conversation context the agent saw\n{transcript}\n\n"
        f"## The agent's output (the thing you are grading)\n{case.agent_output}\n\n"
        f"## Tools the agent actually invoked during this turn\n{tool_calls}\n"
        "(This list is ground truth from the runtime. If the output claims an "
        "action that has no corresponding tool invocation here, the action did "
        "NOT happen.)\n\n"
        f"## Criteria (score each from {SCORE_MIN} to {SCORE_MAX}, integers only)\n"
        f"{criteria_block}\n\n"
        "## Output format\n"
        "Reply with STRICT JSON, exactly this shape, nothing else:\n"
        "{\n"
        '  "criteria": [\n'
        '    {"id": "<criterion id>", "score": <1-5 integer>, '
        '"justification": "<one or two sentences>"}\n'
        "  ],\n"
        '  "overall_comment": "<two sentences max on the biggest strength and '
        'biggest weakness>"\n'
        "}\n"
        f"Include every criterion id exactly once: {ids}."
    )


_STRICT_RETRY_SUFFIX = (
    "\n\nIMPORTANT: your previous reply was not valid JSON. Respond again with "
    "ONLY the JSON object described above — no fences, no commentary."
)


# ── Defensive parsing ────────────────────────────────────────────────────────


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a judge reply.

    Tolerates markdown fences and prose before/after the object. Raises
    JudgeParseError when nothing parseable is found.
    """
    stripped = raw_text.strip()
    if not stripped:
        raise JudgeParseError("judge returned an empty reply")
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            raise JudgeParseError(f"no JSON object in judge reply: {stripped[:200]!r}") from None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise JudgeParseError(f"malformed JSON in judge reply: {exc}") from exc
    if not isinstance(parsed, dict):
        raise JudgeParseError(f"judge JSON is not an object: {type(parsed).__name__}")
    return parsed


def _coerce_score(value: Any) -> int:
    """Coerce a judge-supplied score to a clamped int, or raise."""
    if isinstance(value, bool):  # bool is an int subclass; reject explicitly
        raise JudgeParseError(f"score is a boolean: {value!r}")
    if isinstance(value, int):
        num = value
    elif isinstance(value, float):
        num = round(value)
    elif isinstance(value, str):
        try:
            num = round(float(value.strip()))
        except ValueError:
            raise JudgeParseError(f"unparseable score: {value!r}") from None
    else:
        raise JudgeParseError(f"unparseable score: {value!r}")
    return max(SCORE_MIN, min(SCORE_MAX, num))


def parse_judge_output(
    raw_text: str, rubric: Rubric
) -> tuple[list[CriterionScore], str, list[str]]:
    """Parse a judge reply into ``(scores, overall_comment, missing_criteria)``.

    Defensive behavior:
      - strips fences / surrounding prose before JSON decoding
      - clamps scores into [1, 5]; accepts numeric strings and floats
      - drops entries for criterion ids not in the rubric (logged)
      - keeps the FIRST entry when the judge repeats a criterion
      - returns the rubric ids the judge skipped as ``missing_criteria``
    Raises JudgeParseError when zero valid criterion scores can be recovered.
    """
    parsed = _extract_json_object(raw_text)
    raw_criteria = parsed.get("criteria")
    if not isinstance(raw_criteria, list):
        raise JudgeParseError('judge JSON has no "criteria" list')

    known_ids = set(rubric.criterion_ids())
    scores: dict[str, CriterionScore] = {}
    for entry in raw_criteria:
        if not isinstance(entry, dict):
            continue
        criterion_id = str(entry.get("id", "")).strip()
        if criterion_id not in known_ids:
            logger.warning(
                "Judge scored unknown criterion %r for rubric %s — dropped",
                criterion_id,
                rubric.rubric_id,
            )
            continue
        if criterion_id in scores:
            continue  # keep first occurrence
        try:
            score = _coerce_score(entry.get("score"))
        except JudgeParseError as exc:
            logger.warning(
                "Dropping criterion %r for rubric %s: %s",
                criterion_id,
                rubric.rubric_id,
                exc,
            )
            continue
        justification = str(entry.get("justification", "")).strip()
        scores[criterion_id] = CriterionScore(
            criterion_id=criterion_id,
            score=score,
            justification=justification[:_MAX_JUSTIFICATION_CHARS],
        )

    if not scores:
        raise JudgeParseError("judge reply contained no valid criterion scores")

    ordered = [scores[cid] for cid in rubric.criterion_ids() if cid in scores]
    missing = [cid for cid in rubric.criterion_ids() if cid not in scores]
    comment = str(parsed.get("overall_comment", "")).strip()[:_MAX_COMMENT_CHARS]
    return ordered, comment, missing


def compute_overall(scores: list[CriterionScore], rubric: Rubric) -> float | None:
    """Weighted mean of criterion scores. Deterministic — we never ask the
    judge to self-average."""
    weights = {c.id: c.weight for c in rubric.criteria}
    total_weight = 0.0
    total = 0.0
    for item in scores:
        weight = weights.get(item.criterion_id, 1.0)
        total += item.score * weight
        total_weight += weight
    if total_weight == 0:
        return None
    return round(total / total_weight, 3)


# ── Grading one case ─────────────────────────────────────────────────────────


def _response_text(response: Any) -> str:
    parts = [block.text for block in response.message.content if isinstance(block, TextBlock)]
    return "\n".join(parts).strip()


async def grade_case(
    adapter: ModelAdapter,
    rubric: Rubric,
    case: EvalCase,
    *,
    judge_model: str | None = None,
    judge_provider: str | None = None,
    max_attempts: int = 2,
) -> CaseGrade:
    """Grade one case. Fail-safe: adapter errors and unparseable replies (after
    ``max_attempts``) come back as a CaseGrade with ``error`` set."""
    prompt = build_judge_prompt(rubric, case)
    last_error: str | None = None

    for attempt in range(1, max_attempts + 1):
        text = prompt if attempt == 1 else prompt + _STRICT_RETRY_SUFFIX
        try:
            response = await adapter.complete(
                CompletionRequest(
                    messages=[Message(role="user", content=[TextBlock(text=text)])],
                    system=JUDGE_SYSTEM_PROMPT,
                    model=judge_model,
                    max_tokens=1500,
                )
            )
        except Exception as exc:  # noqa: BLE001 — harness must survive one bad case
            logger.warning("Judge call failed for case %s: %s", case.case_id, exc)
            last_error = f"judge call failed: {exc}"
            continue

        try:
            scores, comment, missing = parse_judge_output(_response_text(response), rubric)
        except JudgeParseError as exc:
            logger.warning(
                "Judge reply unparseable for case %s (attempt %d/%d): %s",
                case.case_id,
                attempt,
                max_attempts,
                exc,
            )
            last_error = str(exc)
            continue

        return CaseGrade(
            case_id=case.case_id,
            agent_id=case.agent_id,
            rubric_id=rubric.rubric_id,
            scores=scores,
            overall=compute_overall(scores, rubric),
            judge_comment=comment,
            missing_criteria=missing,
            judge_provider=judge_provider,
            judge_model=judge_model,
        )

    return CaseGrade(
        case_id=case.case_id,
        agent_id=case.agent_id,
        rubric_id=rubric.rubric_id,
        error=last_error or "judge grading failed",
        judge_provider=judge_provider,
        judge_model=judge_model,
    )
