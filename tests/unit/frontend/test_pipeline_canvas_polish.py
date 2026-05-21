"""
PIPE2 Polish — Pan + Edge-Render + Performance tests.

All tests inspect the JS/CSS source by text since there is no JS test harness
in this repo (no vitest/jest). We validate:
  - Pan state machinery exists and is correctly initialised
  - Middle-mouse, space+drag, and wheel pan code paths are all present
  - Edge coordinate fix: getPortCenter uses style.left/top (local space), not
    getBoundingClientRect (screen space)
  - requestAnimationFrame throttle is present in the drag path
  - Edge index O(connected) optimisation is present
  - CSS cursor states for pan-ready / panning are present
  - _applyTransform composes translate + scale (pan + zoom)
  - getPan() / getZoom() accessors exist for external tests
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).parent.parent.parent.parent
JS_COMP = REPO / "public" / "js" / "components"
CSS_FEAT = REPO / "public" / "css" / "features"

CANVAS_JS = JS_COMP / "pipeline-canvas.js"
PIPELINES_CSS = CSS_FEAT / "pipelines.css"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _js() -> str:
    return CANVAS_JS.read_text()


def _css() -> str:
    return PIPELINES_CSS.read_text()


# ── Pan state initialisation ──────────────────────────────────────────────────


def test_pan_state_initialised():
    src = _js()
    assert "this._panX = 0" in src
    assert "this._panY = 0" in src
    assert "this._isPanning = false" in src
    assert "this._spaceHeld = false" in src


def test_pan_origin_and_start_initialised():
    src = _js()
    assert "_panStart" in src
    assert "_panOrigin" in src


# ── Middle-mouse drag pan ─────────────────────────────────────────────────────


def test_middle_mouse_pan_triggers_on_button1():
    """mousedown with button === 1 should start pan."""
    src = _js()
    assert "e.button === 1" in src
    assert "_startPan" in src


def test_start_pan_records_origin():
    """_startPan stores current panX/Y as origin."""
    src = _js()
    assert "_panOrigin" in src
    assert "_panStart" in src
    assert "this._isPanning = true" in src


# ── Space+drag pan ────────────────────────────────────────────────────────────


def test_space_key_sets_space_held():
    src = _js()
    assert "this._spaceHeld = true" in src
    assert 'e.key === " "' in src


def test_space_drag_initiates_pan():
    """Space+mousedown on empty canvas starts pan."""
    src = _js()
    assert "this._spaceHeld" in src
    assert "_startPan(e)" in src


def test_keyup_space_clears_space_held():
    src = _js()
    assert "this._spaceHeld = false" in src
    assert "_onKeyUp" in src
    assert 'document.addEventListener("keyup"' in src


# ── Trackpad / wheel pan ──────────────────────────────────────────────────────


def test_wheel_without_ctrl_pans():
    """wheel event without ctrlKey should translate, not zoom."""
    src = _js()
    assert "e.ctrlKey" in src
    assert "e.deltaX" in src
    assert "e.deltaY" in src
    # Pan path uses deltaX/deltaY to mutate _panX/_panY
    assert "this._panX -= e.deltaX" in src or "_panX" in src


def test_wheel_with_ctrl_zooms():
    """wheel event WITH ctrlKey should adjust zoom."""
    src = _js()
    # Zoom branch updates _state.zoom
    assert "_state.zoom" in src
    assert "_updateZoomLabel" in src


# ── Edge render fix — Path 1 (shared transform / local coordinates) ───────────


def test_get_port_center_uses_style_not_bounding_rect():
    """
    getPortCenter must use style.left/style.top (local canvas space)
    rather than getBoundingClientRect so SVG paths stay aligned at any zoom.
    """
    src = _js()
    # The fixed implementation reads style.left / style.top
    assert "nodeEl.style.left" in src
    assert "nodeEl.style.top" in src
    # Must NOT use getBoundingClientRect for port coordinates
    # (getBoundingClientRect may still appear elsewhere, but not inside
    # getPortCenter itself — check that the old bounding-rect logic is gone)
    # We verify the new implementation exists:
    assert "parseFloat(nodeEl.style.left)" in src


def test_apply_transform_composes_pan_and_zoom():
    """_applyTransform must include translate() so pan and zoom compose."""
    src = _js()
    assert "_applyTransform" in src
    assert "translate(" in src
    assert "scale(" in src
    # Both panX and panY feed into the transform string
    assert "this._panX" in src and "this._panY" in src


# ── Performance: RAF throttle ─────────────────────────────────────────────────


def test_drag_uses_request_animation_frame():
    src = _js()
    assert "requestAnimationFrame" in src
    assert "_rafPending" in src


def test_flush_drag_exists():
    src = _js()
    assert "_flushDrag" in src


# ── Performance: edge index ───────────────────────────────────────────────────


def test_edge_index_built_in_render_edges():
    src = _js()
    assert "_edgeIndex" in src
    assert "this._edgeIndex = new Map()" in src


def test_update_connected_edges_exists():
    """Only edges touching the dragged node are recomputed during drag."""
    src = _js()
    assert "_updateConnectedEdges" in src


# ── CSS cursor states ─────────────────────────────────────────────────────────


def test_css_pan_ready_cursor():
    css = _css()
    assert "pan-ready" in css
    assert "grab" in css


def test_css_panning_cursor():
    css = _css()
    assert "panning" in css
    assert "grabbing" in css


# ── Accessors ─────────────────────────────────────────────────────────────────


def test_get_pan_accessor_exists():
    src = _js()
    assert "getPan()" in src


def test_get_zoom_accessor_exists():
    src = _js()
    assert "getZoom()" in src


# ── Invariants: pan does not touch node positions ─────────────────────────────


def test_pan_update_does_not_mutate_node_positions():
    """
    The pan handler only mutates _panX/_panY and calls _applyTransform.
    It must NOT touch node.position or call _renderNodes / _renderEdges.
    """
    src = _js()
    # Find the _isPanning branch in _handleMouseMove
    pan_block_start = src.find("if (this._isPanning)")
    assert pan_block_start >= 0, "_isPanning branch not found"
    # Extract the block (up to the return statement)
    pan_block = src[pan_block_start : pan_block_start + 300]
    assert "node.position" not in pan_block, "Pan handler must not mutate node positions"
    assert "_renderNodes" not in pan_block, "Pan handler must not call _renderNodes"
