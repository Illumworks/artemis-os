import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREE_MODULE = ROOT / "public/js/components/agent-tree.js"
OPS_SHELL = ROOT / "public/js/features/operations-shell.js"
OPS_CSS = ROOT / "public/css/features/operations.css"


def run_tree_script(source: str) -> dict:
    script = f"""
      import {{
        buildAgentTree,
        createCustomAgentTreeView,
        createAgentTreeView,
        getVisibleAgents,
        normalizeDisplayFolder,
        summarizeAgentTree,
      }} from {json.dumps(TREE_MODULE.as_uri())};
      const agents = [
        {{ id: "marketing.scout.starbridge_researcher", title: "Starbridge Researcher", description: "Tracks Starbridge signals", metadata: {{ display_folder: "Favorites" }}, lastRunAt: "2026-05-20T12:00:00Z", health: "Healthy", schedule: "Daily" }},
        {{ id: "marketing.scout.board_minutes", title: "Board Minutes", description: "Board docs", metadata: {{ display_folder: "Marketing/Priority" }}, lastRunAt: "2026-05-19T12:00:00Z", health: "Needs attention", schedule: "Weekly" }},
        {{ id: "marketing.qualifier.signal_scorer", title: "Signal Qualifier", description: "Scores demand signals", metadata: {{ display_folder: "Marketing/Priority" }}, lastRunAt: "2026-05-18T12:00:00Z", health: "Healthy", schedule: "Manual" }},
        {{ id: "operations.jira.ticket_triage", title: "Ticket Triage", description: "Jira queue", lastRunAt: null, health: "Never", schedule: "Manual" }},
        {{ id: "personal.note_summarizer", title: "Note Summarizer", description: "Meeting notes", lastRunAt: "2026-05-17T12:00:00Z", health: "Healthy", schedule: "Manual" }},
        {{ id: "smoke-test", title: "Smoke Test", description: "Legacy agent", lastRunAt: null, health: "Never", schedule: "Manual" }},
      ];
      {source}
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def test_tree_groups_agent_id_slugs_and_legacy_agents() -> None:
    data = run_tree_script(
        """
        const tree = buildAgentTree(agents);
        console.log(JSON.stringify({
          domains: Object.keys(tree).sort(),
          marketingScout: tree.marketing.scout.map((a) => a.id).sort(),
          legacy: tree.personal.uncategorized.map((a) => a.id),
        }));
        """
    )
    assert data["domains"] == ["marketing", "operations", "personal"]
    assert data["marketingScout"] == [
        "marketing.scout.board_minutes",
        "marketing.scout.starbridge_researcher",
    ]
    assert data["legacy"] == ["smoke-test"]


def test_search_matches_name_description_and_agent_id_only() -> None:
    data = run_tree_script(
        """
        const visible = getVisibleAgents(agents, { query: "starb" }).map((a) => a.id);
        const summary = summarizeAgentTree(agents, { query: "qualifier" });
        console.log(JSON.stringify({ visible, summary }));
        """
    )
    assert data["visible"] == ["marketing.scout.starbridge_researcher"]
    assert data["summary"] == {"domains": 3, "visible": 1, "total": 6}


def test_sort_last_run_and_filter_never_run() -> None:
    data = run_tree_script(
        """
        const sorted = getVisibleAgents(agents, { sort: "last_run" }).map((a) => a.id);
        const never = getVisibleAgents(agents, { filters: { statuses: ["never"], triggers: [] } }).map((a) => a.id).sort();
        console.log(JSON.stringify({ sorted, never }));
        """
    )
    assert set(data["sorted"][-2:]) == {"operations.jira.ticket_triage", "smoke-test"}
    assert data["never"] == ["operations.jira.ticket_triage", "smoke-test"]


def test_filter_or_within_category_and_and_between_categories() -> None:
    data = run_tree_script(
        """
        const visible = getVisibleAgents(agents, {
          filters: { statuses: ["healthy", "warning"], triggers: ["scheduled"] },
          sort: "health",
        }).map((a) => a.id);
        const view = createAgentTreeView(agents, { query: "missing" });
        console.log(JSON.stringify({ visible, firstEmptyCount: view[0].subdomains[0].agents.length }));
        """
    )
    assert data["visible"] == [
        "marketing.scout.board_minutes",
        "marketing.scout.starbridge_researcher",
    ]
    assert data["firstEmptyCount"] == 0


def test_operations_shell_persists_collapse_namespace_and_compact_rows() -> None:
    shell = OPS_SHELL.read_text()
    css = OPS_CSS.read_text()
    assert 'OPS_AGENT_TREE_COLLAPSED_KEY = "artemis.agents.tree.collapsed"' in shell
    assert 'OPS_AGENT_VIEW_MODE_KEY = "artemis.agents.view-mode"' in shell
    assert 'data-ops-action="set-agent-view-mode"' in shell
    assert 'data-ops-action="add-agent-to-folder"' in shell
    assert 'data-ops-action="select-agent"' in shell
    assert "min-height: 50px;" in css


def test_custom_tree_groups_display_folders_and_unsorted_first() -> None:
    data = run_tree_script(
        """
        const view = createCustomAgentTreeView(agents);
        console.log(JSON.stringify({
          roots: view.map((node) => node.id),
          unsorted: view[0].agents.map((agent) => agent.id).sort(),
          marketingTotal: view.find((node) => node.id === "Marketing").total,
          priorityAgents: view.find((node) => node.id === "Marketing").children[0].agents.map((agent) => agent.id).sort(),
          normalized: normalizeDisplayFolder(" / Team / Priority / "),
        }));
        """
    )
    assert data["roots"] == ["Unsorted", "Favorites", "Marketing"]
    assert data["unsorted"] == [
        "operations.jira.ticket_triage",
        "personal.note_summarizer",
        "smoke-test",
    ]
    assert data["marketingTotal"] == 2
    assert data["priorityAgents"] == [
        "marketing.qualifier.signal_scorer",
        "marketing.scout.board_minutes",
    ]
    assert data["normalized"] == "Team/Priority"
