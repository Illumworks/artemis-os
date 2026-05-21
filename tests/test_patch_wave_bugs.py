"""Patch wave walkthrough bug guards."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINES_JS = ROOT / "public/js/features/pipelines.js"
PIPELINES_CSS = ROOT / "public/css/features/pipelines.css"
OPS_SHELL_JS = ROOT / "public/js/features/operations-shell.js"


def test_pipelines_cards_use_switch_and_shared_toast() -> None:
    js = PIPELINES_JS.read_text()
    css = PIPELINES_CSS.read_text()
    assert 'role="switch"' in js
    assert 'aria-checked="${act ? "true" : "false"}"' in js
    assert "bg-toast" in js
    assert "Status will appear in run history." in js
    assert ".pswitch" in css
    assert ".ptst" not in css
    assert "Pause</button>" not in js


def test_agent_provider_draft_drives_model_picker_state() -> None:
    js = OPS_SHELL_JS.read_text()
    assert "function firstModelForProvider" in js
    assert "const draft = agentDraft?.id === agent?.id ? agentDraft : null;" in js
    assert "const config = draft || enriched || agent;" in js
    assert 'if (field === "provider")' in js
    assert "agentDraft.model = firstModelForProvider(value);" in js
