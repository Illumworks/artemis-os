from pathlib import Path

ROOT = Path(__file__).parent.parent
OPS_SHELL = ROOT / "public/js/features/operations-shell.js"
OPS_CSS = ROOT / "public/css/features/operations.css"


def test_reason_codes_render_as_checkbox_multiselect() -> None:
    src = OPS_SHELL.read_text()

    assert "data-ops-reason-code-multiselect" in src
    assert "ops-reason-code-option" in src
    assert 'type="checkbox"' in src
    assert "<select multiple" not in src


def test_reason_codes_patch_from_checked_boxes() -> None:
    src = OPS_SHELL.read_text()

    assert "saveAgentReasonCodes(field)" in src
    assert "querySelectorAll(\"input[type='checkbox']:checked\")" in src
    assert "await api.updateAgent(selectedAgentId, { reasonCodesEmitted });" in src


def test_agents_page_loads_reason_codes_independently() -> None:
    src = OPS_SHELL.read_text()

    assert "async function refreshReasonCodesFromApi()" in src
    assert "api.listReasonCodesApi()" in src
    assert "if (!_reasonCodesLoaded && !_reasonCodesLoading)" in src


def test_reason_codes_multiselect_has_visible_list_styling() -> None:
    css = OPS_CSS.read_text()

    assert ".ops-reason-code-multiselect" in css
    assert "display: grid" in css
    assert ".ops-reason-code-option:has(input:checked)" in css
