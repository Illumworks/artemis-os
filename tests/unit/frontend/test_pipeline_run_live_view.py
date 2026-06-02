"""PIPE5 — Pipeline Run Live-View source-text tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent.parent.parent
PUBLIC = REPO / "public"
JS_COMP = PUBLIC / "js" / "components"
JS_FEAT = PUBLIC / "js" / "features"
JS_CORE = PUBLIC / "js" / "core"
CSS_FEAT = PUBLIC / "css" / "features"


def _node_check(path: Path) -> bool:
    return subprocess.run(["node", "--check", str(path)], capture_output=True).returncode == 0


# ── File existence ────────────────────────────────────────────────────────────


def test_new_files_exist():
    assert (JS_COMP / "pipeline-run-overlay.js").is_file()
    assert (JS_FEAT / "pipeline-run-history.js").is_file()


# ── node --check ──────────────────────────────────────────────────────────────


def test_js_syntax_overlay():
    assert _node_check(JS_COMP / "pipeline-run-overlay.js")


def test_js_syntax_history():
    assert _node_check(JS_FEAT / "pipeline-run-history.js")


def test_js_syntax_canvas():
    assert _node_check(JS_COMP / "pipeline-canvas.js")


def test_js_syntax_pipelines():
    assert _node_check(JS_FEAT / "pipelines.js")


# ── Export symbols ────────────────────────────────────────────────────────────


def test_export_symbols():
    assert "export class PipelineRunOverlay" in (JS_COMP / "pipeline-run-overlay.js").read_text()
    src = (JS_FEAT / "pipeline-run-history.js").read_text()
    assert "export function initPipelineRunHistoryPage" in src
    assert "export function render" in src
    assert "export async function load" in src


# ── Poll loop — canvas ────────────────────────────────────────────────────────


def test_canvas_poll_loop():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    assert "pipeline-run-overlay.js" in src
    assert "PipelineRunOverlay" in src
    assert "_startPolling" in src
    assert "_stopPolling" in src
    assert "1500" in src  # 1.5s poll interval
    assert "_pollStaleCount" in src
    assert "200" in src  # 5-min stale auto-pause
    assert "_maybeStartPolling" in src


def test_canvas_poll_terminal_stop():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    for status in ("succeeded", "failed", "cancelled", "partial_complete"):
        assert status in src
    assert "TERMINAL" in src or "_pollActive = false" in src


def test_canvas_poll_stops_on_destroy():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    destroy_start = src.find("destroy()")
    assert destroy_start >= 0
    assert "_stopPolling" in src[destroy_start : destroy_start + 300]


# ── Node state application ────────────────────────────────────────────────────


def test_canvas_node_states():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    assert "_applyNodeStates" in src
    assert "_applyRunState" in src
    for state in ("pending", "running", "suspended", "succeeded", "failed", "partial_complete"):
        assert f"pcv-node--{state}" in src


def test_canvas_replay_run():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    assert "replayRun" in src
    assert "this._replayRun" in src


# ── Run overlay ───────────────────────────────────────────────────────────────


def test_run_overlay_elements():
    src = (JS_COMP / "pipeline-run-overlay.js").read_text()
    for sel in (
        "pcv-ro-run-id",
        "pcv-ro-status",
        "pcv-ro-progress",
        "pcv-ro-elapsed",
        "pcv-ro-cancel",
        "pcv-ro-approve",
        "pcv-ro-history-link",
    ):
        assert sel in src, f"Missing element: {sel}"


def test_run_overlay_conditional_logic():
    src = (JS_COMP / "pipeline-run-overlay.js").read_text()
    assert "awaiting_approval" in src
    assert "TERMINAL" in src or "terminal" in src.lower()
    assert "hide()" in src or "this.hide" in src
    assert "cancelBtn.disabled = isTerminal" in src
    assert "#/pipeline-run-history" in src
    assert 'setState("view", "pipeline-run-history")' in src


def test_live_run_polling_filters_recent_active_runs():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    assert "ACTIVE_RUN_MAX_AGE_MS" in src
    assert "_isRecentActiveRun" in src
    assert 'sort: "created_at_desc"' in src
    assert '"skipped"' in src


def test_pipeline_run_history_is_real_shell_view():
    nav = (JS_CORE / "navigation.js").read_text()
    ops = (JS_FEAT / "operations-shell.js").read_text()
    home = (JS_FEAT / "home.js").read_text()
    assert "PIPELINE_RUN_HISTORY_VIEW" in nav
    assert "operations/pipeline-run-history" in nav
    assert "initPipelineRunHistoryPage" in ops
    assert "loadPipelineRunHistoryShell" in home


# ── Run history page ──────────────────────────────────────────────────────────


def test_run_history_table():
    src = (JS_FEAT / "pipeline-run-history.js").read_text()
    for col in ("Pipeline", "Started", "Duration", "Status", "Trigger", "Nodes", "Actions"):
        assert col in src
    assert "_statusFilter" in src or "statusFilter" in src
    assert "_sortBy" in src or "sortBy" in src
    assert "data-status" in src
    assert "data-sort" in src


def test_run_history_actions():
    src = (JS_FEAT / "pipeline-run-history.js").read_text()
    assert "prh-cancel" in src and "cancelPipelineRunApi" in src
    assert "prh-retry" in src and "retryPipelineRunApi" in src
    assert "prh-open-canvas" in src and "_openCanvasReplay" in src
    assert "artemis:open-pipeline-canvas" in src
    assert "listAllPipelineRunsApi" in src


# ── Stale toast text + integrations ──────────────────────────────────────────


def test_stale_toast_removed():
    canvas = (JS_COMP / "pipeline-canvas.js").read_text()
    pls = (JS_FEAT / "pipelines.js").read_text()
    assert "execution wired in PIPE4" not in canvas
    assert "execution engine arrives in PIPE4" not in pls
    assert "Watch progress on canvas" in canvas
    assert "Watch progress on canvas" in pls


def test_pipelines_feature_integrations():
    src = (JS_FEAT / "pipelines.js").read_text()
    assert "artemis:open-pipeline-canvas" in src
    assert "replayRun" in src
    assert "pb-mini-progress" in src


# ── CSS ───────────────────────────────────────────────────────────────────────


def test_css_node_state_classes():
    css = (CSS_FEAT / "pipelines.css").read_text()
    for state in ("pending", "running", "suspended", "succeeded", "failed", "partial_complete"):
        assert f".pcv-node--{state}" in css
    assert "pcv-node-pulse" in css  # running animation


def test_css_run_overlay():
    css = (CSS_FEAT / "pipelines.css").read_text()
    for sel in (
        ".pcv-run-overlay",
        ".pcv-run-overlay--hidden",
        ".pcv-ro-header",
        ".pcv-ro-status",
        ".pcv-ro-progress",
        ".pcv-ro-elapsed",
        ".pcv-ro-cancel",
        ".pcv-ro-approve",
        ".pcv-ro-history-link",
    ):
        assert sel in css, f"Missing CSS: {sel}"


def test_css_run_history():
    css = (CSS_FEAT / "pipelines.css").read_text()
    for sel in (".prh-table", ".prh-th", ".prh-td", ".prh-row", ".prh-actions"):
        assert sel in css


def test_css_no_raw_hex_in_pipe2_block():
    import re

    css = (CSS_FEAT / "pipelines.css").read_text()
    pipe2_start = css.find("PIPE2")
    assert pipe2_start >= 0
    raw_hex = re.findall(r"(?<!-)#[0-9a-fA-F]{3,8}\b", css[pipe2_start:])
    allowed = {"#fff", "#000"}
    assert [h for h in raw_hex if h.lower() not in allowed] == []


# ── API + backend ─────────────────────────────────────────────────────────────


def test_api_helpers():
    src = (JS_CORE / "api.js").read_text()
    assert "listAllPipelineRunsApi" in src
    assert "/api/pipeline-runs" in src
    assert "retryPipelineRunApi" in src


def test_backend_list_all_runs():
    routes = (REPO / "artemis" / "pipelines" / "routes.py").read_text()
    repo = (REPO / "artemis" / "pipelines" / "repository.py").read_text()
    assert "/api/pipeline-runs" in routes
    assert "list_all_runs" in routes
    assert "list_all_pipeline_runs" in repo
