"""PIPE3 walkthrough 2 frontend regression checks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent.parent.parent
PUBLIC = REPO / "public"
JS = PUBLIC / "js"
COMP = JS / "components"
NCF = COMP / "node-config-forms"

PIPELINES = JS / "features" / "pipelines.js"
CRON_UTILS = COMP / "cron-utils.js"
NODE_CARD = COMP / "pipeline-node-card.js"
SCHED_FORM = NCF / "trigger-scheduled-form.js"
HUMAN_GATE = NCF / "human-gate-form.js"
PIPELINES_CSS = PUBLIC / "css" / "features" / "pipelines.css"


def test_pipeline_kebab_closes_on_outside_click_and_action():
    src = PIPELINES.read_text()

    assert 'document.addEventListener("click"' in src
    assert 'target?.closest(".pmenu")' in src
    assert "_openMenuId = null;" in src
    assert "async function handlePipelineMenuAction" in src
    assert src.find("async function handlePipelineMenuAction") < src.find(
        'if (action === "restore")'
    )


def test_cron_title_is_bound_to_sync_and_node_summary_humanizes():
    form = SCHED_FORM.read_text()
    node_card = NODE_CARD.read_text()

    assert "ncf-cron-title" in form
    assert "titleEl.textContent = desc" in form
    assert ".ncf-cron-title" in PIPELINES_CSS.read_text()
    assert 'import { describeCron } from "./cron-utils.js"' in node_card
    assert "describeCron(cfg.cron)" in node_card


def test_weekly_range_round_trips_to_weekly_mode():
    script = f"""
      import {{ parseCron, compileCron }} from {json.dumps(CRON_UTILS.as_uri())};
      const parsed = parseCron("0 9 * * 1-5");
      const recompiled = compileCron(parsed.mode, parsed.fields);
      console.log(JSON.stringify({{ parsed, recompiled }}));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPO,
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)

    assert payload["parsed"]["mode"] == "weekly"
    assert payload["parsed"]["fields"]["days"] == [1, 2, 3, 4, 5]
    assert payload["recompiled"] == "0 9 * * 1-5"


def test_no_cron_mode_persistence_overrides_saved_cron():
    src = SCHED_FORM.read_text()

    assert "parseCron(savedCron)" in src
    assert "localStorage" not in src
    assert "sessionStorage" not in src


def test_human_gate_freetext_and_escalation_to_are_wired():
    src = HUMAN_GATE.read_text()

    assert 'data-ncf="approvers"' in src
    assert 'data-ncf="escalation_to"' in src
    assert "isValidEmail(q)" in src
    assert "_wireEmailPicker(msWrap, approvers)" in src
    assert "_wireEmailPicker(escalationWrap, escalationTo)" in src
    assert "escalation_to: escalationPicker.values()" in src
    assert "Specify at least one escalation approver." in src
    assert 'onTimeoutEl?.value === "escalate"' in src
