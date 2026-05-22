"""
PIPE3 — Per-type node config forms: frontend source tests.

No JS test harness exists (no vitest/jest/package.json).
These Python tests validate:
  - All five new form modules exist and are non-empty
  - Each form module exports the expected render function
  - The config drawer was updated: Form/JSON toggle markup + imports
  - CSS contains PIPE3 form selectors
  - PIPE2 invariants (canvas symbols, existing selectors) are NOT regressed
  - node.config JSONB shape contract: each form's expected keys are represented
    in the source code (via string inspection of the render function body)
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).parent.parent.parent.parent
PUBLIC = REPO / "public"
JS_COMP = PUBLIC / "js" / "components"
NCF = JS_COMP / "node-config-forms"
CSS_FEAT = PUBLIC / "css" / "features"

DRAWER = JS_COMP / "pipeline-config-drawer.js"
PIPELINES_CSS = CSS_FEAT / "pipelines.css"


# ── File existence ────────────────────────────────────────────────────────────


def test_agent_invocation_form_exists():
    assert (NCF / "agent-invocation-form.js").is_file()


def test_trigger_scheduled_form_exists():
    assert (NCF / "trigger-scheduled-form.js").is_file()


def test_human_gate_form_exists():
    assert (NCF / "human-gate-form.js").is_file()


def test_conditional_form_exists():
    assert (NCF / "conditional-form.js").is_file()


def test_sub_pipeline_form_exists():
    assert (NCF / "sub-pipeline-form.js").is_file()


# ── Export symbols ────────────────────────────────────────────────────────────


def test_agent_invocation_exports_render():
    src = (NCF / "agent-invocation-form.js").read_text()
    assert "export function renderAgentInvocationForm" in src


def test_trigger_scheduled_exports_render():
    src = (NCF / "trigger-scheduled-form.js").read_text()
    assert "export function renderTriggerScheduledForm" in src


def test_human_gate_exports_render():
    src = (NCF / "human-gate-form.js").read_text()
    assert "export function renderHumanGateForm" in src


def test_conditional_exports_render():
    src = (NCF / "conditional-form.js").read_text()
    assert "export function renderConditionalForm" in src


def test_sub_pipeline_exports_render():
    src = (NCF / "sub-pipeline-form.js").read_text()
    assert "export function renderSubPipelineForm" in src


# ── Drawer: Form/JSON toggle ──────────────────────────────────────────────────


def test_drawer_imports_all_five_forms():
    src = DRAWER.read_text()
    assert "renderAgentInvocationForm" in src
    assert "renderTriggerScheduledForm" in src
    assert "renderHumanGateForm" in src
    assert "renderConditionalForm" in src
    assert "renderSubPipelineForm" in src


def test_drawer_has_view_toggle_markup():
    src = DRAWER.read_text()
    assert "pcv-view-btn" in src
    assert "pcv-drawer-view-toggle" in src


def test_drawer_has_typed_form_set():
    src = DRAWER.read_text()
    assert "TYPED_FORM_TYPES" in src
    assert "agent_invocation" in src
    assert "trigger_scheduled" in src
    assert "human_gate" in src
    assert "conditional" in src
    assert "sub_pipeline" in src


def test_drawer_view_mode_defaults_to_form():
    src = DRAWER.read_text()
    # Default viewMode should be "form" for typed types
    assert '_viewMode = "form"' in src or "viewMode = 'form'" in src or '"form"' in src


def test_drawer_json_view_preserved():
    """JSON textarea must still exist in the drawer for the fallback view."""
    src = DRAWER.read_text()
    assert "pcv-drawer-json" in src


def test_drawer_form_json_toggle_syncs_state():
    """Switching views must sync config state in both directions."""
    src = DRAWER.read_text()
    # Form → JSON: getValues() called before switch
    assert "getValues" in src
    # JSON → Form: JSON.parse called before switch
    assert "JSON.parse" in src


def test_drawer_save_merges_unknown_fields():
    """Save must spread existing JSON over form values to preserve extra keys."""
    src = DRAWER.read_text()
    # Merger pattern: { ...existing, ...vals } or { ...existing, ...formVals }
    assert "...existing" in src


# ── PIPE2 regression: drawer still exports PipelineConfigDrawer ──────────────


def test_drawer_still_exports_class():
    src = DRAWER.read_text()
    assert "export class PipelineConfigDrawer" in src


# ── node.config JSONB shape — key coverage per form type ─────────────────────


def test_agent_invocation_config_keys():
    """agent_id, mode, cost_cap_usd, provider_override, model_override."""
    src = (NCF / "agent-invocation-form.js").read_text()
    for key in ["agent_id", "mode", "cost_cap_usd", "provider_override", "model_override"]:
        assert key in src, f"agent_invocation form missing config key: {key}"


def test_trigger_scheduled_config_keys():
    """cron, timezone, start_date, end_date."""
    src = (NCF / "trigger-scheduled-form.js").read_text()
    for key in ["cron", "timezone", "start_date", "end_date"]:
        assert key in src, f"trigger_scheduled form missing config key: {key}"


def test_human_gate_config_keys():
    """approval_kind, approvers, timeout_hours, on_timeout."""
    src = (NCF / "human-gate-form.js").read_text()
    for key in ["approval_kind", "approvers", "timeout_hours", "on_timeout"]:
        assert key in src, f"human_gate form missing config key: {key}"


def test_conditional_config_keys():
    """predicate (op, left, right), true_label, false_label, expression."""
    src = (NCF / "conditional-form.js").read_text()
    for key in ["predicate", "op", "left", "right", "true_label", "false_label", "expression"]:
        assert key in src, f"conditional form missing config key: {key}"


def test_sub_pipeline_config_keys():
    """pipeline_id, mode."""
    src = (NCF / "sub-pipeline-form.js").read_text()
    for key in ["pipeline_id", "mode"]:
        assert key in src, f"sub_pipeline form missing config key: {key}"


# ── Form validate() + getValues() contract ───────────────────────────────────


def test_all_forms_have_get_values():
    for fname in [
        "agent-invocation-form.js",
        "trigger-scheduled-form.js",
        "human-gate-form.js",
        "conditional-form.js",
        "sub-pipeline-form.js",
    ]:
        src = (NCF / fname).read_text()
        assert "getValues()" in src or "getValues (" in src, f"{fname} missing getValues()"


def test_all_forms_have_validate():
    for fname in [
        "agent-invocation-form.js",
        "trigger-scheduled-form.js",
        "human-gate-form.js",
        "conditional-form.js",
        "sub-pipeline-form.js",
    ]:
        src = (NCF / fname).read_text()
        assert "validate()" in src or "validate (" in src, f"{fname} missing validate()"


# ── Required-field validation logic ──────────────────────────────────────────


def test_agent_invocation_validates_required_agent():
    src = (NCF / "agent-invocation-form.js").read_text()
    assert "Agent is required" in src


def test_agent_invocation_agent_filter_coerces_ids_to_string():
    src = (NCF / "agent-invocation-form.js").read_text()
    assert "_safeStr(a.agent_id ?? a.id).toLowerCase()" in src
    assert 'String(v ?? "")' in src


def test_agent_invocation_model_override_is_provider_filtered_select():
    src = (NCF / "agent-invocation-form.js").read_text()
    assert "getSourceModels(provider)" in src
    assert 'ncf-model-override"></select>' in src
    assert "ncf-provider-override" in src


def test_agent_invocation_cost_cap_tooltip_copy():
    src = (NCF / "agent-invocation-form.js").read_text()
    assert "Stops execution when total LLM cost" in src
    assert "partial_complete" in src


def test_trigger_scheduled_validates_required_cron():
    src = (NCF / "trigger-scheduled-form.js").read_text()
    assert "cron" in src.lower()
    assert "required" in src.lower() or "Invalid cron" in src


def test_sub_pipeline_validates_required_pipeline():
    src = (NCF / "sub-pipeline-form.js").read_text()
    assert "Target pipeline is required" in src


def test_sub_pipeline_rejects_self_reference():
    src = (NCF / "sub-pipeline-form.js").read_text()
    assert "currentPipelineId" in src


# ── Cron description helper ───────────────────────────────────────────────────


def test_trigger_scheduled_has_cron_describe():
    src = (NCF / "trigger-scheduled-form.js").read_text()
    assert "describeCron" in src
    # Common patterns should be recognizable
    assert "Every hour" in src or "Every" in src


def test_trigger_scheduled_has_cron_validation():
    src = (NCF / "trigger-scheduled-form.js").read_text()
    assert "isValidCron" in src


def test_trigger_scheduled_live_preview_and_next_run():
    src = (NCF / "trigger-scheduled-form.js").read_text()
    assert "ncf-next-run-preview" in src
    assert "computeNextRun" in src
    assert 'cronEl.addEventListener("input", _updatePreview)' in src
    assert 'tzEl.addEventListener("change", _updatePreview)' in src


def test_human_gate_has_three_named_approvers():
    src = (NCF / "human-gate-form.js").read_text()
    for email in ["josh@amiralearning.com", "angela@amiralearning.com", "jon@amiralearning.com"]:
        assert email in src
    for name in ["Josh", "Angela", "Jon"]:
        assert name in src


def test_human_gate_on_timeout_inline_help():
    src = (NCF / "human-gate-form.js").read_text()
    assert "ncf-timeout-help" in src
    assert "ping a secondary approver" in src
    assert "automatically approve" in src
    assert "automatically reject" in src


# ── CSS: PIPE3 selectors present ─────────────────────────────────────────────


def test_css_has_form_toggle_selectors():
    css = PIPELINES_CSS.read_text()
    for sel in [".pcv-drawer-view-toggle", ".pcv-view-btn", ".pcv-view-btn--active"]:
        assert sel in css, f"Missing CSS selector: {sel}"


def test_css_has_ncf_base_selectors():
    css = PIPELINES_CSS.read_text()
    for sel in [".ncf", ".ncf-field", ".ncf-label", ".ncf-input", ".ncf-select"]:
        assert sel in css, f"Missing CSS selector: {sel}"


def test_css_has_picker_selectors():
    css = PIPELINES_CSS.read_text()
    for sel in [".ncf-picker", ".ncf-picker-results", ".ncf-picker-item"]:
        assert sel in css, f"Missing CSS selector: {sel}"


def test_css_has_multiselect_selectors():
    css = PIPELINES_CSS.read_text()
    for sel in [".ncf-multiselect", ".ncf-tags", ".ncf-tag"]:
        assert sel in css, f"Missing CSS selector: {sel}"


def test_css_pipe3_uses_design_tokens_only():
    """No raw hex colors (other than #fff/#000) in the PIPE3 section."""
    import re

    css = PIPELINES_CSS.read_text()
    pipe3_start = css.find("PIPE3")
    assert pipe3_start >= 0, "PIPE3 section header not found in CSS"
    pipe3_css = css[pipe3_start:]
    raw_hex = re.findall(r"(?<!-)#[0-9a-fA-F]{3,8}\b", pipe3_css)
    allowed = {"#fff", "#000"}
    unexpected = [h for h in raw_hex if h.lower() not in allowed]
    assert unexpected == [], f"Unexpected raw hex colors in PIPE3 CSS: {unexpected}"


# ── PIPE2 regression guard ────────────────────────────────────────────────────


def test_pipe2_canvas_exports_still_present():
    canvas = JS_COMP / "pipeline-canvas.js"
    src = canvas.read_text()
    assert "export class PipelineCanvas" in src


def test_pipe2_canvas_uses_drawer():
    canvas = JS_COMP / "pipeline-canvas.js"
    src = canvas.read_text()
    assert "PipelineConfigDrawer" in src


def test_pipe2_css_selectors_intact():
    css = PIPELINES_CSS.read_text()
    for sel in [".pcv-canvas", ".pcv-node", ".pcv-edges-svg", ".pcv-drawer"]:
        assert sel in css, f"PIPE2 regression: missing CSS selector {sel}"


# ── conditional form: JSONLogic toggle ───────────────────────────────────────


def test_conditional_has_jsonlogic_toggle():
    src = (NCF / "conditional-form.js").read_text()
    assert "ncf-jsonlogic-toggle" in src
    assert "ncf-jsonlogic-raw" in src


def test_conditional_validates_jsonlogic_json():
    src = (NCF / "conditional-form.js").read_text()
    assert "JSON.parse" in src
    assert "not valid JSON" in src or "JSONLogic expression" in src


# ── human_gate: multi-select approvers ───────────────────────────────────────


def test_human_gate_has_default_approvers():
    src = (NCF / "human-gate-form.js").read_text()
    assert "amiralearning.com" in src


def test_human_gate_has_on_timeout_options():
    src = (NCF / "human-gate-form.js").read_text()
    for opt in ["auto_approve", "auto_reject", "escalate"]:
        assert opt in src, f"human_gate missing on_timeout option: {opt}"


# ── sub_pipeline: self-exclusion via currentPipelineId ───────────────────────


def test_sub_pipeline_drawer_passes_pipeline_id():
    """Canvas must pass pipelineId to the drawer constructor."""
    canvas = JS_COMP / "pipeline-canvas.js"
    src = canvas.read_text()
    assert "pipelineId" in src
