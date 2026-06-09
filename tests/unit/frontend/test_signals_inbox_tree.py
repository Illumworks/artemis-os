import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TREE_MODULE = ROOT / "public/js/components/signal-tree.js"
MKT_JS = ROOT / "public/js/features/marketing-os.js"
MKT_CSS = ROOT / "public/css/features/marketing-os.css"


def run_tree_script(source: str) -> dict:
    script = f"""
      globalThis.localStorage = {{
        data: new Map(),
        getItem(key) {{ return this.data.has(key) ? this.data.get(key) : null; }},
        setItem(key, value) {{ this.data.set(key, String(value)); }},
      }};
      globalThis.document = {{
        createElement() {{
          return {{
            _text: "",
            set textContent(value) {{ this._text = String(value ?? ""); }},
            get innerHTML() {{
              return this._text
                .replaceAll("&", "&amp;")
                .replaceAll("<", "&lt;")
                .replaceAll(">", "&gt;")
                .replaceAll('"', "&quot;");
            }},
          }};
        }},
      }};
      const mod = await import({json.dumps(TREE_MODULE.as_uri())});
      const signals = [
        {{
          id: 1,
          headline: "Pinellas mandate hearing",
          summary: "Pinellas County moved literacy screener language into the board agenda.",
          whyFlagged: "The scout flagged repeated board-level literacy action in a named district.",
          signalStatus: "qualified",
          urgencyTier: "hot",
          state: "FL",
          districtId: "Pinellas County",
          reasonCodes: [{{ code: "POLICY_LIT_MANDATE", confidence: 0.91 }}],
          relatedSignalsCount: 2,
          discoveredBy: "board_minutes",
          agentRunId: "run-enrich1-gate1-001",
          pipelineRun: {{
            id: "run_1234567890abcdef",
            pipelineId: "marketing-pipeline",
            pipelineName: "Marketing Pipeline",
            status: "awaiting_approval",
            startedAt: "2026-05-21T11:00:00Z",
          }},
          approval: {{ id: 9, href: "#approvals/9" }},
          createdAt: "2026-05-21T12:00:00Z",
          qualificationJson: {{
            districtContext: {{
              resolved: true,
              districtName: "Pinellas County",
              districtState: "FL",
              districtTier: "D2",
              districtEnrollment: 9800,
              districtSupported: true,
              onSkipList: true,
            }},
            scores: [{{ campaignFamily: "state_screener", passedHardFilters: true }}],
          }},
        }},
        {{
          id: 2,
          headline: "Indiana grant opens",
          summary: "IDOE opened a literacy implementation grant.",
          signalStatus: "pending_qualification",
          urgencyTier: "standard",
          state: "IN",
          districtId: "Indianapolis Public Schools",
          reasonCodes: [{{ code: "FUNDING_LITERACY_GRANT", confidence: 0.8 }}],
          createdAt: "2026-05-20T12:00:00Z",
        }},
        {{
          id: 3,
          headline: "Texas enrichment mention",
          summary: "A leader mentioned summer reading interest.",
          signalStatus: "suppressed_stale",
          urgencyTier: "enrichment",
          state: "TX",
          districtId: "Austin ISD",
          reasonCodes: [{{ code: "LEADERSHIP_LITERACY_PRIORITY", confidence: 0.6 }}],
          createdAt: "2026-05-19T12:00:00Z",
        }},
      ].map(mod.normalizeSignal);
      {source}
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def test_each_grouping_mode_builds_expected_tree() -> None:
    data = run_tree_script(
        """
        const out = {};
        for (const mode of mod.SIGNAL_GROUPS) {
          out[mode] = mod.buildSignalTree(signals, mode).map((g) => ({
            key: g.key,
            count: g.signals.length + g.children.reduce((sum, c) => sum + c.signals.length, 0),
            childCount: g.children.length,
          }));
        }
        console.log(JSON.stringify(out));
        """
    )
    assert [g["key"] for g in data["state"]][:2] == ["qualified", "pending_qualification"]
    assert {g["key"] for g in data["reason"]} == {
        "FUNDING_LITERACY_GRANT",
        "LEADERSHIP_LITERACY_PRIORITY",
        "POLICY_LIT_MANDATE",
    }
    assert data["geography"][0]["childCount"] == 1
    assert [g["key"] for g in data["urgency"]] == ["hot", "standard", "enrichment"]
    assert [g["key"] for g in data["pipeline"]] == [
        "Marketing Pipeline · run_1234",
        "No pipeline run",
    ]
    assert data["flat"][0]["count"] == 3


def test_search_and_filter_chips_compound() -> None:
    data = run_tree_script(
        """
        const filtered = mod.filterSignals(signals, {
          query: "Pinellas",
          filters: { urgencies: ["hot"], statuses: ["qualified"], reasons: [], geographies: ["FL"] },
        }).map((s) => s.id);
        const blocked = mod.filterSignals(signals, {
          query: "Pinellas",
          filters: { urgencies: ["standard"], statuses: ["qualified"], reasons: [], geographies: ["FL"] },
        }).map((s) => s.id);
        console.log(JSON.stringify({ filtered, blocked }));
        """
    )
    assert data == {"filtered": [1], "blocked": []}


def test_sort_reorders_within_group() -> None:
    data = run_tree_script(
        """
        const rows = mod.sortSignals([signals[1], signals[0], signals[2]], "urgency").map((s) => s.id);
        console.log(JSON.stringify({ rows }));
        """
    )
    assert data["rows"] == [1, 2, 3]


def test_detail_panel_and_empty_state_render() -> None:
    data = run_tree_script(
        """
        const detail = mod.renderSignalDetailPanel(signals[0]);
        const empty = mod.renderSignalInboxTree([], { mode: "state" });
        const emptyRuns = mod.renderSignalInboxTree([], {
          mode: "state",
          emptyMessage: "Last 3 pipeline runs produced 0 signals. Configure scout connectors to start ingesting data.",
        });
        console.log(JSON.stringify({
          hasAudit: detail.includes("Qualifier Audit"),
          hasReason: detail.includes("POLICY_LIT_MANDATE 91%"),
          hasRunBadge: detail.includes("Marketing Pipeline") && detail.includes("View pipeline run"),
          hasApproval: detail.includes("Awaiting Gate 1"),
          hasWhyFlagged: detail.includes("The scout flagged repeated board-level literacy action"),
          hasScoutIdentity: detail.includes("board_minutes") && detail.includes("Trace run-enrich1"),
          hasRelatedCount: detail.includes("2 related signals seen"),
          hasSkipList: detail.includes("skip list"),
          hasExpand: detail.includes("Expand to full signal"),
          hasPipelineCta: empty.includes("Trigger marketing pipeline manually"),
          hasConnectorCta: emptyRuns.includes("Configure scout connectors"),
        }));
        """
    )
    assert data == {
        "hasAudit": True,
        "hasReason": True,
        "hasRunBadge": True,
        "hasApproval": True,
        "hasWhyFlagged": True,
        "hasScoutIdentity": True,
        "hasRelatedCount": True,
        "hasSkipList": True,
        "hasExpand": True,
        "hasPipelineCta": True,
        "hasConnectorCta": True,
    }


def test_manual_cluster_toolbar_and_row_selection_render() -> None:
    data = run_tree_script(
        """
        const extraSignals = [
          ...signals,
          mod.normalizeSignal({
            id: 4,
            headline: "Pinellas follow-up hearing",
            summary: "Second board hearing reinforces the same literacy push.",
            signalStatus: "qualified",
            urgencyTier: "hot",
            state: "FL",
            districtId: "Pinellas County",
            reasonCodes: [{ code: "POLICY_LIT_MANDATE", confidence: 0.88 }],
            createdAt: "2026-05-22T12:00:00Z",
          }),
        ];
        const html = mod.renderSignalInboxTree(extraSignals, {
          mode: "flat",
          selectedId: 1,
          selectedSignalIds: [1, 4],
        });
        console.log(JSON.stringify({
          hasManualClusterBar: html.includes("Manual cluster:"),
          hasManualClusterAction: html.includes("Group into a cluster → Start a campaign"),
          hasSelectedCount: html.includes("2 selected"),
          hasSelectableCheckboxes: html.includes('data-signal-select="1"') && html.includes('data-signal-select="4"'),
          pendingSignalNotSelectable: !html.includes('data-signal-select="2"'),
        }));
        """
    )
    assert data == {
        "hasManualClusterBar": True,
        "hasManualClusterAction": True,
        "hasSelectedCount": True,
        "hasSelectableCheckboxes": True,
        "pendingSignalNotSelectable": True,
    }


def test_local_storage_keys_and_static_wiring() -> None:
    data = run_tree_script(
        """
        mod.writeSignalGroupMode("reason");
        mod.writeCollapsedSignalGroups({ "state:qualified": true });
        console.log(JSON.stringify({
          groupKey: mod.SIGNAL_GROUP_KEY,
          collapsedKey: mod.SIGNAL_COLLAPSED_KEY,
          mode: mod.readSignalGroupMode(),
          collapsed: mod.readCollapsedSignalGroups(),
        }));
        """
    )
    js = MKT_JS.read_text()
    css = MKT_CSS.read_text()
    assert data["groupKey"] == "artemis.signals.group-by"
    assert data["collapsedKey"] == "artemis.signals.tree.collapsed"
    assert data["mode"] == "reason"
    assert data["collapsed"] == {"state:qualified": True}
    assert "renderSignalInboxTree" in js
    assert "grid-template-columns: minmax(360px, 1fr) minmax(300px, 380px);" in css
