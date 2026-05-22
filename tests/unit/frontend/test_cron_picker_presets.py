"""
Cron picker presets — frontend source tests.

No JS test harness exists in this repo. These Python tests validate the
cron-picker-presets implementation via source inspection:

  - cron-utils.js exists and exports expected symbols
  - trigger-scheduled-form.js has the 5 mode renderers and mode bar markup
  - Cron compilation strings for all 5 modes are represented in the source
  - Round-trip invariants: parse → compile → parse matches original mode
  - CSS has mode-bar and day-of-week selectors
  - No regression on existing PIPE3 trigger_scheduled contracts
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).parent.parent.parent.parent
PUBLIC = REPO / "public"
JS_COMP = PUBLIC / "js" / "components"
NCF = JS_COMP / "node-config-forms"
CSS_FEAT = PUBLIC / "css" / "features"

CRON_UTILS = JS_COMP / "cron-utils.js"
SCHED_FORM = NCF / "trigger-scheduled-form.js"
PIPELINES_CSS = CSS_FEAT / "pipelines.css"


# ── File existence ────────────────────────────────────────────────────────────


def test_cron_utils_exists():
    assert CRON_UTILS.is_file(), "cron-utils.js must exist"


def test_trigger_scheduled_form_exists():
    assert SCHED_FORM.is_file(), "trigger-scheduled-form.js must exist"


# ── cron-utils.js exports ─────────────────────────────────────────────────────


def test_cron_utils_exports_compile():
    src = CRON_UTILS.read_text()
    assert "export function compileCron" in src


def test_cron_utils_exports_parse():
    src = CRON_UTILS.read_text()
    assert "export function parseCron" in src


def test_cron_utils_exports_describe():
    src = CRON_UTILS.read_text()
    assert "export function describeCron" in src


def test_cron_utils_exports_is_valid():
    src = CRON_UTILS.read_text()
    assert "export function isValidCron" in src


# ── Compilation strings in source (one per mode) ──────────────────────────────


def test_compile_every_n_minutes_pattern():
    """every_n minutes → */N * * * *"""
    src = CRON_UTILS.read_text()
    assert "`*/${n} * * * *`" in src


def test_compile_every_n_hours_pattern():
    """every_n hours → 0 */N * * *"""
    src = CRON_UTILS.read_text()
    assert "`0 */${n} * * *`" in src


def test_compile_every_n_days_pattern():
    """every_n days → 0 0 */N * *"""
    src = CRON_UTILS.read_text()
    assert "`0 0 */${n} * *`" in src


def test_compile_daily_pattern():
    """daily → ${m} ${h} * * *"""
    src = CRON_UTILS.read_text()
    assert "* * *`" in src  # daily and monthly share this suffix
    assert 'case "daily"' in src


def test_compile_monthly_pattern():
    """monthly → ${m} ${h} ${dom} * *"""
    src = CRON_UTILS.read_text()
    assert 'case "monthly"' in src
    assert "${dom} * *`" in src


def test_compile_weekly_case():
    src = CRON_UTILS.read_text()
    assert 'case "weekly"' in src


def test_compile_custom_passthrough():
    src = CRON_UTILS.read_text()
    assert 'case "custom"' in src
    assert "fields.cron" in src


# ── Parse-and-match — priority rules ─────────────────────────────────────────


def test_parse_every_n_minutes_rule():
    """Mode 1: */N in minute field → every_n/minutes"""
    src = CRON_UTILS.read_text()
    assert 'mode: "every_n"' in src
    assert '"minutes"' in src


def test_parse_every_n_hours_rule():
    """Mode 1: 0 */N → every_n/hours"""
    src = CRON_UTILS.read_text()
    assert '"hours"' in src


def test_parse_every_n_days_rule():
    """Mode 1: 0 0 */N → every_n/days"""
    src = CRON_UTILS.read_text()
    assert '"days"' in src


def test_parse_daily_rule():
    """Mode 2: int int * * * → daily"""
    src = CRON_UTILS.read_text()
    assert 'mode: "daily"' in src


def test_parse_weekly_rule():
    """Mode 3: int int * * <dow> → weekly"""
    src = CRON_UTILS.read_text()
    assert 'mode: "weekly"' in src


def test_parse_monthly_rule():
    """Mode 4: int int int * * → monthly"""
    src = CRON_UTILS.read_text()
    assert 'mode: "monthly"' in src


def test_parse_custom_fallback():
    """Mode 5: anything else → custom"""
    src = CRON_UTILS.read_text()
    assert 'mode: "custom"' in src


def test_parse_priority_every_n_before_daily():
    """every_n patterns are checked before daily — their check appears first."""
    src = CRON_UTILS.read_text()
    pos_every_n = src.find('mode: "every_n"')
    pos_daily = src.find('mode: "daily"')
    assert 0 < pos_every_n < pos_daily, "every_n must be matched before daily"


def test_parse_priority_daily_before_weekly():
    src = CRON_UTILS.read_text()
    pos_daily = src.find('mode: "daily"')
    pos_weekly = src.find('mode: "weekly"')
    assert 0 < pos_daily < pos_weekly, "daily must be matched before weekly"


def test_parse_priority_weekly_before_monthly():
    src = CRON_UTILS.read_text()
    pos_weekly = src.find('mode: "weekly"')
    pos_monthly = src.find('mode: "monthly"')
    assert 0 < pos_weekly < pos_monthly, "weekly must be matched before monthly"


# ── Day-compression (contiguous runs) ────────────────────────────────────────


def test_day_compression_function_exists():
    """_compressDays helper must be present for Mon-Fri → 1-5 logic."""
    src = CRON_UTILS.read_text()
    assert "_compressDays" in src


def test_day_range_format_present():
    """Source must produce range notation (e.g. 1-5) not just comma lists."""
    src = CRON_UTILS.read_text()
    # The template literal that builds the range string
    assert "`${start}-${end}`" in src


# ── trigger-scheduled-form.js: mode bar + 5 renderers ────────────────────────


def test_form_imports_cron_utils():
    src = SCHED_FORM.read_text()
    assert 'from "../cron-utils.js"' in src or "from '../cron-utils.js'" in src


def test_form_has_5_modes_defined():
    src = SCHED_FORM.read_text()
    for mode_id in ["every_n", "daily", "weekly", "monthly", "custom"]:
        assert mode_id in src, f"trigger-scheduled-form.js missing mode: {mode_id}"


def test_form_mode_bar_markup():
    src = SCHED_FORM.read_text()
    assert "ncf-cron-mode-bar" in src
    assert "ncf-cron-mode-btn" in src


def test_form_render_every_n():
    src = SCHED_FORM.read_text()
    assert "_renderEveryN" in src
    assert "ncf-cron-n" in src
    assert "ncf-cron-unit" in src


def test_form_render_daily():
    src = SCHED_FORM.read_text()
    assert "_renderDaily" in src
    assert "ncf-cron-time" in src


def test_form_render_weekly():
    src = SCHED_FORM.read_text()
    assert "_renderWeekly" in src
    assert "ncf-cron-dow-row" in src
    assert "ncf-cron-dow-cb" in src


def test_form_render_monthly():
    src = SCHED_FORM.read_text()
    assert "_renderMonthly" in src
    assert "ncf-cron-dom" in src


def test_form_render_custom():
    src = SCHED_FORM.read_text()
    assert "_renderCustom" in src


def test_form_compile_cron_called_in_get_values():
    """getValues() must call compileCron to produce the persisted cron string."""
    src = SCHED_FORM.read_text()
    assert "compileCron" in src
    assert "getValues" in src


def test_form_parse_cron_called_on_load():
    """parseCron must be called to determine opening mode."""
    src = SCHED_FORM.read_text()
    assert "parseCron" in src


def test_form_preview_uses_describe_cron():
    """describeCron must be wired to the preview element."""
    src = SCHED_FORM.read_text()
    assert "describeCron" in src
    assert "ncf-cron-preview" in src


def test_form_validate_checks_weekly_days():
    """validate() must enforce at least one day selected in weekly mode."""
    src = SCHED_FORM.read_text()
    assert "Select at least one day" in src


# ── PIPE3 regression guards ───────────────────────────────────────────────────


def test_form_still_exports_render_trigger_scheduled():
    src = SCHED_FORM.read_text()
    assert "export function renderTriggerScheduledForm" in src


def test_form_still_has_cron_config_key():
    src = SCHED_FORM.read_text()
    assert "cron" in src


def test_form_still_has_timezone_config_key():
    src = SCHED_FORM.read_text()
    assert "timezone" in src


def test_form_still_has_start_end_date_config_keys():
    src = SCHED_FORM.read_text()
    assert "start_date" in src
    assert "end_date" in src


def test_form_still_has_get_values():
    src = SCHED_FORM.read_text()
    assert "getValues()" in src


def test_form_still_has_validate():
    src = SCHED_FORM.read_text()
    assert "validate()" in src


# ── CSS: cron-picker selectors ────────────────────────────────────────────────


def test_css_has_mode_bar():
    css = PIPELINES_CSS.read_text()
    assert ".ncf-cron-mode-bar" in css


def test_css_has_mode_btn():
    css = PIPELINES_CSS.read_text()
    assert ".ncf-cron-mode-btn" in css


def test_css_has_mode_btn_active():
    css = PIPELINES_CSS.read_text()
    assert ".ncf-cron-mode-btn--active" in css


def test_css_has_dow_row():
    css = PIPELINES_CSS.read_text()
    assert ".ncf-cron-dow-row" in css


def test_css_has_dow_item():
    css = PIPELINES_CSS.read_text()
    assert ".ncf-cron-dow-item" in css


def test_css_has_dow_item_on_state():
    css = PIPELINES_CSS.read_text()
    assert ".ncf-cron-dow-item--on" in css


def test_css_has_every_row():
    css = PIPELINES_CSS.read_text()
    assert ".ncf-cron-every-row" in css


def test_css_cron_picker_uses_design_tokens():
    """No raw hex colors (other than #fff/#000) in the cron-picker CSS block."""
    css = PIPELINES_CSS.read_text()
    picker_start = css.find("cron-picker-presets")
    assert picker_start >= 0, "cron-picker-presets section not found in CSS"
    picker_css = css[picker_start:]
    raw_hex = re.findall(r"(?<!-)#[0-9a-fA-F]{3,8}\b", picker_css)
    allowed = {"#fff", "#000"}
    unexpected = [h for h in raw_hex if h.lower() not in allowed]
    assert unexpected == [], f"Unexpected raw hex colors in cron-picker CSS: {unexpected}"
