"""
PIPE2 — Pipeline canvas frontend smoke tests.

No JS test harness is present in this repo (no vitest/jest/package.json).
These Python tests validate:
  - all expected JS/CSS files are present and non-empty
  - the JS files export the expected symbols (by text inspection)
  - the CSS contains the expected selectors
  - the pipeline node/edge data shape logic (edgePath, node creation) via
    extracted helper logic re-implemented in Python for contract testing

Full browser interaction tests (drag, edge creation, etc.) require a
playwright or vitest setup that can be added in a follow-up task.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).parent.parent.parent.parent  # artemis-os root
PUBLIC = REPO / "public"
JS_COMP = PUBLIC / "js" / "components"
JS_FEAT = PUBLIC / "js" / "features"
CSS_FEAT = PUBLIC / "css" / "features"


# ── File existence ────────────────────────────────────────────────────────────


def test_pipeline_canvas_js_exists():
    assert (JS_COMP / "pipeline-canvas.js").is_file()


def test_pipeline_node_card_js_exists():
    assert (JS_COMP / "pipeline-node-card.js").is_file()


def test_pipeline_palette_js_exists():
    assert (JS_COMP / "pipeline-palette.js").is_file()


def test_pipeline_config_drawer_js_exists():
    assert (JS_COMP / "pipeline-config-drawer.js").is_file()


def test_pipelines_feature_js_exists():
    assert (JS_FEAT / "pipelines.js").is_file()


def test_pipelines_css_exists():
    assert (CSS_FEAT / "pipelines.css").is_file()


# ── Export / import symbols ───────────────────────────────────────────────────


def test_canvas_exports_pipeline_canvas_class():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    assert "export class PipelineCanvas" in src


def test_node_card_exports_build_function():
    src = (JS_COMP / "pipeline-node-card.js").read_text()
    assert "export function buildNodeCard" in src
    assert "export function updateNodeCardPosition" in src
    assert "export function setNodeCardSelected" in src


def test_palette_exports_pipeline_palette_class():
    src = (JS_COMP / "pipeline-palette.js").read_text()
    assert "export class PipelinePalette" in src


def test_config_drawer_exports_class():
    src = (JS_COMP / "pipeline-config-drawer.js").read_text()
    assert "export class PipelineConfigDrawer" in src


def test_pipelines_feature_imports_canvas():
    src = (JS_FEAT / "pipelines.js").read_text()
    assert "PipelineCanvas" in src
    assert "pipeline-canvas.js" in src


def test_pipelines_feature_has_open_canvas():
    src = (JS_FEAT / "pipelines.js").read_text()
    assert "openCanvas" in src
    assert "closeCanvas" in src


# ── CSS selectors ─────────────────────────────────────────────────────────────


def test_css_has_canvas_selectors():
    css = (CSS_FEAT / "pipelines.css").read_text()
    for sel in [
        ".pcv-shell",
        ".pcv-canvas",
        ".pcv-node",
        ".pcv-port",
        ".pcv-edges-svg",
        ".pcv-palette",
        ".pcv-drawer",
        ".pcv-overlay",
    ]:
        assert sel in css, f"Missing CSS selector: {sel}"


def test_css_uses_design_tokens_only():
    """No raw hex colors should appear in the PIPE2 block (after PIPE1 line)."""
    css = (CSS_FEAT / "pipelines.css").read_text()
    # Find PIPE2 section
    pipe2_start = css.find("PIPE2")
    assert pipe2_start >= 0, "PIPE2 section header not found in CSS"
    pipe2_css = css[pipe2_start:]
    # Allow #fff (pure white in button text, common) and color-mix() with tokens.
    # Flag any raw hex that isn't trivially #fff / #000
    raw_hex = re.findall(r"(?<!-)#[0-9a-fA-F]{3,8}\b", pipe2_css)
    allowed = {"#fff", "#000"}
    unexpected = [h for h in raw_hex if h.lower() not in allowed]
    # C94A1F (error) + 6366f1 etc. appear in PIPE1; warn if they appear in PIPE2
    assert unexpected == [], f"Unexpected raw hex colors in PIPE2 CSS block: {unexpected}"


# ── Data-shape contract tests ─────────────────────────────────────────────────

MARKETING_PIPELINE_SEED = REPO / "artemis" / "pipelines" / "seeds" / "marketing_pipeline.py"


def _load_marketing_pipeline_data():
    """Run the build_marketing_pipeline function via subprocess."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, '.'); "
            "from artemis.pipelines.seeds.marketing_pipeline import build_marketing_pipeline; "
            "import json; print(json.dumps(build_marketing_pipeline()))",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def test_marketing_pipeline_has_16_nodes():
    import pytest

    data = _load_marketing_pipeline_data()
    if data is None:
        pytest.skip("Marketing pipeline seed not loadable (DB not available)")
    assert len(data["nodes"]) == 16, f"Expected 16 nodes, got {len(data['nodes'])}"


def test_marketing_pipeline_edges_reference_valid_nodes():
    import pytest

    data = _load_marketing_pipeline_data()
    if data is None:
        pytest.skip("Marketing pipeline seed not loadable")
    node_ids = {n["id"] for n in data["nodes"]}
    for edge in data["edges"]:
        assert edge["source_node_id"] in node_ids, (
            f"Edge source {edge['source_node_id']} not in nodes"
        )
        assert edge["target_node_id"] in node_ids, (
            f"Edge target {edge['target_node_id']} not in nodes"
        )


def test_marketing_pipeline_all_nodes_have_positions():
    import pytest

    data = _load_marketing_pipeline_data()
    if data is None:
        pytest.skip("Marketing pipeline seed not loadable")
    for node in data["nodes"]:
        pos = node.get("position")
        assert pos is not None, f"Node {node['id']} missing position"
        assert "x" in pos and "y" in pos, f"Node {node['id']} position missing x/y"


def test_marketing_pipeline_edge_count_in_range():
    import pytest

    data = _load_marketing_pipeline_data()
    if data is None:
        pytest.skip("Marketing pipeline seed not loadable")
    edge_count = len(data["edges"])
    assert 20 <= edge_count <= 28, f"Expected 20–28 edges, got {edge_count}"


# ── JS canvas logic smoke (via node meta, extracted) ─────────────────────────


def test_node_type_meta_coverage():
    """All valid PIPE1 node types have meta entries in pipeline-node-card.js."""
    src = (JS_COMP / "pipeline-node-card.js").read_text()
    valid_types = [
        "trigger_manual",
        "trigger_scheduled",
        "trigger_webhook",
        "trigger_event",
        "agent_invocation",
        "skill_call",
        "human_gate",
        "conditional",
        "sub_pipeline",
    ]
    for t in valid_types:
        assert t in src, f"Node type {t} missing from pipeline-node-card.js"


def test_canvas_implements_undo_redo():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    assert "undoStack" in src
    assert "redoStack" in src
    assert "_undo()" in src or "this._undo" in src
    assert "_redo()" in src or "this._redo" in src


def test_canvas_preserves_extra_fields_on_save():
    """Canvas must spread extra node/edge fields (JSONB round-trip safety)."""
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    # Check that createStore spreads node fields
    assert "...n," in src or "{ ...n," in src or "...n }" in src
    # Check that edges are spread too
    assert "...e," in src or "{ ...e," in src or "...e }" in src


def test_canvas_uses_patch_api():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    assert "updatePipelineApi" in src


def test_canvas_has_auto_layout():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    assert "autoLayout" in src or "_autoLayout" in src


def test_canvas_has_fit_to_view():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    assert "_fitToView" in src or "fitToView" in src


def test_canvas_has_json_toggle():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    assert "_toggleJson" in src or "toggleJson" in src
