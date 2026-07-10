"""CLI for the Agent Report Card.

Examples::

    uv run python -m artemis.evals
    uv run python -m artemis.evals --agents artemis callie
    uv run python -m artemis.evals --provider anthropic --model claude-sonnet-4-6
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from artemis.evals.harness import DEFAULT_AGENT_IDS, run_report_card
from artemis.evals.report import write_run_artifacts


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Grade named-agent outputs against rubrics.")
    parser.add_argument(
        "--agents",
        nargs="+",
        default=list(DEFAULT_AGENT_IDS),
        help=f"Agent ids to grade (default: {' '.join(DEFAULT_AGENT_IDS)}).",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Judge provider id (anthropic, claude-code, ...). "
        "Default: ARTEMIS_EVALS_JUDGE_PROVIDER or the resolver cascade.",
    )
    parser.add_argument("--model", default=None, help="Judge model override.")
    parser.add_argument("--label", default="report-card", help="Report label stem.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Artifact root (default ~/.artemis/evals or ARTEMIS_EVALS_DIR).",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir) if args.output_dir else None
    run = await run_report_card(
        agent_ids=args.agents,
        judge_provider=args.provider,
        judge_model=args.model,
        label=args.label,
        write_artifacts=False,
    )
    json_path, md_path = write_run_artifacts(run, output_dir=output_dir)
    for agent in run.agents:
        overall = f"{agent.overall_mean:.2f}" if agent.overall_mean is not None else "n/a"
        print(
            f"{agent.agent_id:<10} overall={overall}/5  "
            f"graded={agent.graded_count}/{agent.case_count}  failed={agent.failed_count}"
        )
    print(f"JSON: {json_path}")
    print(f"Markdown: {md_path}")
    return 0 if all(a.failed_count == 0 for a in run.agents) else 1


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _build_parser().parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
