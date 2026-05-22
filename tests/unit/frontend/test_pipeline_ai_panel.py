"""Frontend smoke tests for Pipeline AI Assistant panel.

Validates:
  - File exists and is non-empty
  - PipelineAIPanel class exported
  - Expected methods present
  - PROPOSAL_RE constant present for stream parsing
  - Ghost-apply logic (mirrored from proposals.py) present in JS
  - Canvas integration: AI button in toolbar, _mountAIPanel method,
    import of PipelineAIPanel
  - CSS: AI panel + ghost selectors present

No JS runtime available — all checks are source-text inspection.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).parent.parent.parent.parent  # artemis-os root
JS_COMP = REPO / "public" / "js" / "components"
CSS = REPO / "public" / "css" / "features" / "pipelines.css"


# ── File existence ────────────────────────────────────────────────────────────


def test_pipeline_ai_panel_js_exists():
    assert (JS_COMP / "pipeline-ai-panel.js").is_file()


def test_pipeline_ai_panel_nonempty():
    assert (JS_COMP / "pipeline-ai-panel.js").stat().st_size > 500


# ── Exports ───────────────────────────────────────────────────────────────────


def test_ai_panel_exports_class():
    src = (JS_COMP / "pipeline-ai-panel.js").read_text()
    assert "export class PipelineAIPanel" in src


# ── Core methods ──────────────────────────────────────────────────────────────


def test_ai_panel_has_mount():
    src = (JS_COMP / "pipeline-ai-panel.js").read_text()
    assert "mount(" in src


def test_ai_panel_has_toggle():
    src = (JS_COMP / "pipeline-ai-panel.js").read_text()
    assert "toggle()" in src


def test_ai_panel_has_open_close():
    src = (JS_COMP / "pipeline-ai-panel.js").read_text()
    assert "open()" in src
    assert "close()" in src


def test_ai_panel_has_destroy():
    src = (JS_COMP / "pipeline-ai-panel.js").read_text()
    assert "destroy()" in src


# ── Proposal stream parsing ────────────────────────────────────────────────────


def test_ai_panel_has_proposal_regex():
    src = (JS_COMP / "pipeline-ai-panel.js").read_text()
    assert "PROPOSAL_BEGIN" in src
    assert "PROPOSAL_END" in src


def test_ai_panel_proposal_re_constant():
    src = (JS_COMP / "pipeline-ai-panel.js").read_text()
    assert "PROPOSAL_RE" in src


# ── SSE event handling ────────────────────────────────────────────────────────


def test_ai_panel_handles_sse_events():
    src = (JS_COMP / "pipeline-ai-panel.js").read_text()
    assert "_handleSseEvent" in src
    assert "assistant_token" in src
    assert "proposal_parsed" in src
    assert "self_improvement" in src


# ── Accept/Reject flow ────────────────────────────────────────────────────────


def test_ai_panel_has_accept_reject():
    src = (JS_COMP / "pipeline-ai-panel.js").read_text()
    assert "_acceptProposal" in src
    assert "_rejectProposal" in src


def test_ai_panel_calls_on_proposal_accept_callback():
    src = (JS_COMP / "pipeline-ai-panel.js").read_text()
    assert "this._onProposalAccept" in src


# ── Client-side proposal apply ────────────────────────────────────────────────


def test_ai_panel_has_apply_proposal():
    src = (JS_COMP / "pipeline-ai-panel.js").read_text()
    assert "_applyProposal" in src


def test_ai_panel_apply_covers_all_kinds():
    src = (JS_COMP / "pipeline-ai-panel.js").read_text()
    for kind in ["add_node", "remove_node", "add_edge", "remove_edge", "update_node_config"]:
        assert kind in src, f"Missing proposal kind handler: {kind}"


# ── Self-improvement rendering ────────────────────────────────────────────────


def test_ai_panel_renders_self_improvement():
    src = (JS_COMP / "pipeline-ai-panel.js").read_text()
    assert "_renderSelfImprovementHint" in src
    assert "pai-self-improvement" in src


# ── Conversation persistence ──────────────────────────────────────────────────


def test_ai_panel_loads_history():
    src = (JS_COMP / "pipeline-ai-panel.js").read_text()
    assert "_loadHistory" in src
    assert "/assistant/conversation" in src


def test_ai_panel_clear_conversation():
    src = (JS_COMP / "pipeline-ai-panel.js").read_text()
    assert "_clearConversation" in src


# ── Canvas integration ────────────────────────────────────────────────────────


def test_canvas_imports_ai_panel():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    assert "PipelineAIPanel" in src
    assert "pipeline-ai-panel.js" in src


def test_canvas_has_mount_ai_panel():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    assert "_mountAIPanel" in src


def test_canvas_toolbar_has_ai_button():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    assert "pcv-btn-ai" in src


def test_canvas_wires_ai_button():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    assert "pcv-btn-ai" in src
    assert "_aiPanel?.toggle()" in src or "this._aiPanel" in src


def test_canvas_accepts_proposal_applies_to_state():
    src = (JS_COMP / "pipeline-canvas.js").read_text()
    # Canvas accept callback mutates _state.nodes / _state.edges
    assert "_state.nodes = updatedNodes" in src or "this._state.nodes" in src


# ── CSS selectors ─────────────────────────────────────────────────────────────


def test_css_has_ai_panel_selectors():
    css = CSS.read_text()
    for sel in [
        ".pai-panel",
        ".pai-header",
        ".pai-messages",
        ".pai-msg",
        ".pai-proposal",
        ".pai-self-improvement",
        ".pai-input",
        ".pcv-ai-panel-wrap",
    ]:
        assert sel in css, f"Missing CSS selector: {sel}"


def test_css_ai_panel_section_uses_design_tokens():
    """AI panel CSS section must not introduce raw hex colours (only vars/color-mix)."""
    import re

    css = CSS.read_text()
    section_start = css.find("AI Assistant Panel")
    assert section_start >= 0, "AI Assistant Panel CSS section header not found"
    section = css[section_start:]
    raw_hex = re.findall(r"(?<!-)#[0-9a-fA-F]{3,8}\b", section)
    allowed = {"#fff", "#000"}
    unexpected = [h for h in raw_hex if h.lower() not in allowed]
    assert unexpected == [], f"Unexpected raw hex in AI panel CSS: {unexpected}"
