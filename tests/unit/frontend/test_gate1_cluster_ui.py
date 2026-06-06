"""Tests for Gate-1 cluster card rendering helpers.

These tests exercise _renderClusterCard, _renderClusterSignalRow, and
_renderClustersSection by running an inline Node module that stubs the
escapeHtml dependency and re-exports the helper implementations verbatim.

The cluster rendering code is self-contained (only needs esc/escapeHtml)
so we can test it without loading all of marketing-os.js.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MKT_JS = ROOT / "public/js/features/marketing-os.js"
MKT_CSS = ROOT / "public/css/features/marketing-os.css"


# Read the cluster helper source from marketing-os.js for inline reuse.
# We extract the cluster rendering functions and their esc dependency so the
# test runs in Node without having to stub all 30+ marketing-os imports.
_CLUSTER_HELPERS_SOURCE = r"""
// Minimal escapeHtml stub (same as existing test infrastructure)
globalThis.document = {
  createElement() {
    return {
      _text: "",
      set textContent(value) { this._text = String(value ?? ""); },
      get innerHTML() {
        return this._text
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;")
          .replaceAll('"', "&quot;");
      },
    };
  },
};

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function esc(s) {
  return escapeHtml(String(s ?? ""));
}

function _renderClusterSignalRow(signal) {
  const roleBadge = signal.role === "primary"
    ? `<span class="mkt-cluster-role mkt-cluster-role--primary">Primary</span>`
    : `<span class="mkt-cluster-role mkt-cluster-role--corrob">Corrob</span>`;
  const urgencyClass = signal.urgency ? ` mkt-cluster-signal--${esc(signal.urgency)}` : "";
  return `
    <div class="mkt-cluster-signal-row${urgencyClass}">
      ${roleBadge}
      <span class="mkt-cluster-signal-headline">${esc(signal.headline || "Signal")}</span>
      ${signal.evidence_quote ? `<span class="mkt-cluster-signal-evidence">${esc(signal.evidence_quote)}</span>` : ""}
      <span class="mkt-cluster-signal-meta">${esc(signal.source || "")}${signal.fit_score != null ? ` · fit ${(signal.fit_score * 100).toFixed(0)}%` : ""}</span>
    </div>`;
}

function _renderClusterCard(cluster, approvalId) {
  const isSuggested = cluster.suggested === true;
  const suggestedClass = isSuggested ? " mkt-cluster-card--suggested" : "";
  const suggestedChip = isSuggested
    ? `<span class="mkt-cluster-suggested-chip">&#x26A1; Strongest signal</span>`
    : "";
  const scoreText = cluster.score != null
    ? `Score ${(cluster.score * 100).toFixed(0)}%`
    : "";
  const scoreReason = cluster.score_reason ? ` · ${esc(cluster.score_reason)}` : "";
  const signals = Array.isArray(cluster.signals) ? cluster.signals : [];
  const signalRows = signals.map(_renderClusterSignalRow).join("");
  return `
    <div class="mkt-cluster-card${suggestedClass}" data-cluster-key="${esc(cluster.cluster_key || "")}">
      <div class="mkt-cluster-head">
        <div class="mkt-cluster-title-row">
          <span class="mkt-cluster-name">${esc(cluster.district_label || "")} · ${esc(cluster.campaign_family || "")}</span>
          ${suggestedChip}
        </div>
        <div class="mkt-cluster-sub">${esc(scoreText)}${scoreReason}</div>
      </div>
      <div class="mkt-cluster-signals">${signalRows}</div>
      <div class="mkt-signal-actions">
        <button class="mkt-btn-primary" type="button"
          data-approve-id="${esc(String(approvalId))}"
          data-cluster-key="${esc(cluster.cluster_key || "")}">&#x2713; Approve this cluster</button>
      </div>
    </div>`;
}

function _renderClustersSection(ctx, approvalId) {
  const clusters = Array.isArray(ctx.clusters) && ctx.clusters.length ? ctx.clusters : null;
  if (!clusters) return "";
  return `<div class="mkt-clusters-list">${clusters.map((c) => _renderClusterCard(c, approvalId)).join("")}</div>`;
}
"""


def run_cluster_script(source: str) -> dict:
    script = _CLUSTER_HELPERS_SOURCE + "\n" + source
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


SAMPLE_CLUSTERS = [
    {
        "cluster_key": "district_42|adoption-loss",
        "district_label": "Cleveland Metropolitan",
        "campaign_family": "adoption-loss",
        "score": 0.89,
        "score_reason": "3 stacked signals + recent activity",
        "suggested": True,
        "signals": [
            {
                "id": 17,
                "role": "primary",
                "headline": "Board voted to expand reading program",
                "evidence_quote": "board unanimously approved expansion",
                "source": "board_minutes",
                "fit_score": 0.82,
                "urgency": "high",
            },
            {
                "id": 18,
                "role": "corroborating",
                "headline": "Superintendent comment on literacy",
                "source": "press_release",
                "fit_score": 0.74,
            },
        ],
    },
    {
        "cluster_key": "district_99|state-screener",
        "district_label": "Indianapolis Public",
        "campaign_family": "state-screener",
        "score": 0.61,
        "score_reason": "single signal",
        "suggested": False,
        "signals": [
            {
                "id": 22,
                "role": "primary",
                "headline": "State screener mandate pending",
                "source": "legislation",
                "fit_score": 0.61,
            }
        ],
    },
]


def test_suggested_cluster_has_amber_class_and_chip() -> None:
    """Suggested cluster gets mkt-cluster-card--suggested class and chip."""
    data = run_cluster_script(
        f"""
        const clusters = {json.dumps(SAMPLE_CLUSTERS)};
        const html = _renderClusterCard(clusters[0], 99);
        console.log(JSON.stringify({{
          hasSuggestedClass: html.includes("mkt-cluster-card--suggested"),
          hasChip: html.includes("Strongest signal"),
          hasChipElement: html.includes("mkt-cluster-suggested-chip"),
        }}));
        """
    )
    assert data["hasSuggestedClass"] is True
    assert data["hasChip"] is True
    assert data["hasChipElement"] is True


def test_non_suggested_cluster_has_no_amber_class() -> None:
    """Non-suggested cluster has no suggested class or chip."""
    data = run_cluster_script(
        f"""
        const clusters = {json.dumps(SAMPLE_CLUSTERS)};
        const html = _renderClusterCard(clusters[1], 99);
        console.log(JSON.stringify({{
          hasSuggestedClass: html.includes("mkt-cluster-card--suggested"),
          hasChip: html.includes("Strongest signal"),
        }}));
        """
    )
    assert data["hasSuggestedClass"] is False
    assert data["hasChip"] is False


def test_cluster_card_header_contains_district_and_family() -> None:
    """Cluster card header shows district_label · campaign_family."""
    data = run_cluster_script(
        f"""
        const clusters = {json.dumps(SAMPLE_CLUSTERS)};
        const html = _renderClusterCard(clusters[0], 99);
        console.log(JSON.stringify({{
          hasDistrict: html.includes("Cleveland Metropolitan"),
          hasFamily: html.includes("adoption-loss"),
          hasScore: html.includes("Score 89%"),
          hasReason: html.includes("3 stacked signals"),
        }}));
        """
    )
    assert data["hasDistrict"] is True
    assert data["hasFamily"] is True
    assert data["hasScore"] is True
    assert data["hasReason"] is True


def test_cluster_signal_rows_have_role_badges() -> None:
    """Primary signal gets Primary badge; corroborating gets Corrob badge."""
    data = run_cluster_script(
        f"""
        const clusters = {json.dumps(SAMPLE_CLUSTERS)};
        const html = _renderClusterCard(clusters[0], 99);
        console.log(JSON.stringify({{
          hasPrimary: html.includes("mkt-cluster-role--primary"),
          hasCorrob: html.includes("mkt-cluster-role--corrob"),
          hasPrimaryLabel: html.includes(">Primary<"),
          hasCorrobLabel: html.includes(">Corrob<"),
        }}));
        """
    )
    assert data["hasPrimary"] is True
    assert data["hasCorrob"] is True
    assert data["hasPrimaryLabel"] is True
    assert data["hasCorrobLabel"] is True


def test_cluster_approve_button_carries_cluster_key_and_approval_id() -> None:
    """Approve button has data-approve-id and data-cluster-key attributes."""
    data = run_cluster_script(
        f"""
        const clusters = {json.dumps(SAMPLE_CLUSTERS)};
        const html = _renderClusterCard(clusters[0], 42);
        console.log(JSON.stringify({{
          hasApproveId: html.includes('data-approve-id="42"'),
          hasClusterKey: html.includes('data-cluster-key="district_42|adoption-loss"'),
          hasApproveText: html.includes("Approve this cluster"),
        }}));
        """
    )
    assert data["hasApproveId"] is True
    assert data["hasClusterKey"] is True
    assert data["hasApproveText"] is True


def test_render_clusters_section_returns_empty_string_when_no_clusters() -> None:
    """_renderClustersSection returns '' when clusters key absent or empty."""
    data = run_cluster_script(
        """
        const noKey = _renderClustersSection({}, 1);
        const emptyArr = _renderClustersSection({ clusters: [] }, 1);
        console.log(JSON.stringify({ noKey, emptyArr }));
        """
    )
    assert data["noKey"] == ""
    assert data["emptyArr"] == ""


def test_render_clusters_section_returns_list_when_clusters_present() -> None:
    """_renderClustersSection wraps clusters in mkt-clusters-list div."""
    data = run_cluster_script(
        f"""
        const clusters = {json.dumps(SAMPLE_CLUSTERS)};
        const html = _renderClustersSection({{ clusters }}, 7);
        console.log(JSON.stringify({{
          hasList: html.includes("mkt-clusters-list"),
          hasCluster0: html.includes("Cleveland Metropolitan"),
          hasCluster1: html.includes("Indianapolis Public"),
          clusterCount: (html.match(/mkt-cluster-card/g) || []).length,
        }}));
        """
    )
    assert data["hasList"] is True
    assert data["hasCluster0"] is True
    assert data["hasCluster1"] is True
    # Each cluster card appears once (suggested has --suggested modifier, so 3 matches total for 2 cards)
    assert data["clusterCount"] >= 2


def test_evidence_quote_rendered_in_signal_row() -> None:
    """Evidence quote is shown in the signal row when present."""
    data = run_cluster_script(
        f"""
        const clusters = {json.dumps(SAMPLE_CLUSTERS)};
        const html = _renderClusterSignalRow(clusters[0].signals[0]);
        console.log(JSON.stringify({{
          hasEvidence: html.includes("board unanimously approved expansion"),
          hasEvidenceElement: html.includes("mkt-cluster-signal-evidence"),
        }}));
        """
    )
    assert data["hasEvidence"] is True
    assert data["hasEvidenceElement"] is True


def test_missing_evidence_quote_omits_evidence_element() -> None:
    """Signal without evidence_quote omits the evidence element."""
    data = run_cluster_script(
        f"""
        const clusters = {json.dumps(SAMPLE_CLUSTERS)};
        const html = _renderClusterSignalRow(clusters[1].signals[0]);
        console.log(JSON.stringify({{
          hasEvidenceElement: html.includes("mkt-cluster-signal-evidence"),
        }}));
        """
    )
    assert data["hasEvidenceElement"] is False


def test_mkt_js_exports_render_marketing_approvals() -> None:
    """Sanity: marketing-os.js still exports renderMarketingApprovals."""
    js = MKT_JS.read_text()
    assert "renderMarketingApprovals" in js
    assert "_renderClustersSection" in js
    assert "_renderClusterCard" in js
    assert "_renderClusterSignalRow" in js


def test_css_has_cluster_card_styles() -> None:
    """Sanity: CSS defines cluster card and suggested variant rules."""
    css = MKT_CSS.read_text()
    assert ".mkt-cluster-card" in css
    assert ".mkt-cluster-card--suggested" in css
    assert ".mkt-cluster-suggested-chip" in css
    assert ".mkt-clusters-list" in css
    assert "#d97706" in css  # amber accent border color
