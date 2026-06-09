"""Phase 1 Marketing Intelligence — UI smoke tests.

Exercises the two read-only render entry points added by the Phase 1 UI brief:

  - `renderTrendContextSection(trendContext)` — the trend block injected into the
    initiation/Gate-1 review modal alongside the existing ENRICH1 enrichment.
  - `renderMarketingPrioritization(payload, opts)` — the "Where to focus" view
    fed by GET /api/marketing/intel/prioritization.

Pattern follows tests/unit/frontend/test_signals_inbox_tree.py: launch a node
subprocess that imports the production module with a minimal DOM stub (so
escapeHtml works) and inspect the rendered HTML string.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MKT_JS = ROOT / "public/js/features/marketing-os.js"
MKT_CSS = ROOT / "public/css/features/marketing-os.css"
NAV_JS = ROOT / "public/js/core/navigation.js"
API_JS = ROOT / "public/js/core/api.js"
HOME_JS = ROOT / "public/js/features/home.js"


def _run_node(snippet: str) -> dict:
    """Run a node module snippet with DOM stubs and parse the JSON it prints."""
    script = f"""
      globalThis.localStorage = {{
        data: new Map(),
        getItem(key) {{ return this.data.has(key) ? this.data.get(key) : null; }},
        setItem(key, value) {{ this.data.set(key, String(value)); }},
        removeItem(key) {{ this.data.delete(key); }},
      }};
      globalThis.window = {{ location: {{ hash: '' }} }};
      globalThis.document = {{
        createElement() {{
          return {{
            _text: '',
            set textContent(value) {{ this._text = String(value ?? ''); }},
            get innerHTML() {{
              return this._text
                .replaceAll('&', '&amp;')
                .replaceAll('<', '&lt;')
                .replaceAll('>', '&gt;')
                .replaceAll('"', '&quot;');
            }},
          }};
        }},
        getElementById() {{ return null; }},
        querySelector() {{ return null; }},
        addEventListener() {{}},
      }};
      const mod = await import({json.dumps(MKT_JS.as_uri())});
      {snippet}
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


# ── Piece 1: trend context section ────────────────────────────────────────


def test_trend_context_resolved_renders_full_block() -> None:
    data = _run_node(
        """
        const trendContext = {
          resolved: true,
          asOf: '2026-06-04T12:00:00Z',
          theme: 'literacy',
          region: 'FL',
          momentum: {
            window_days: 90,
            bucket_days: 7,
            buckets: [
              { bucket_start: '2026-04-01', bucket_end: '2026-04-08', count: 1 },
              { bucket_start: '2026-04-08', bucket_end: '2026-04-15', count: 3 },
              { bucket_start: '2026-04-15', bucket_end: '2026-04-22', count: 5 },
            ],
            current_window_count: 9,
            prior_window_count: 3,
            delta_ratio: 3.0,
          },
          comparables: {
            comparable_count: 7,
            sample_districts: [
              { name: 'Pinellas' }, { name: 'Orange' }, { name: 'Duval' },
            ],
          },
          decisionHistory: {
            priorApproves: 5,
            priorRejects: 1,
            topMatches: [
              { observationId: 11, category: 'gate1_approval', decision: 'approved', summary: 'Approved similar campaign in Pinellas' },
              { observationId: 12, category: 'gate1_rejection', decision: 'rejected', summary: 'Rejected stale campaign' },
            ],
          },
        };
        const html = mod.renderTrendContextSection(trendContext);
        console.log(JSON.stringify({
          hasSection: html.includes('mkt-trend-context'),
          hasScopeLabel: html.includes('literacy/FL'),
          hasMomentumUp: html.includes('up ~3'),
          hasMomentumDetail: html.includes('9 signals in last 90d'),
          hasPriorCount: html.includes('3 prior'),
          hasSparkline: html.includes('mkt-trend-sparkline'),
          hasComparablesCount: html.includes('7 comparable districts'),
          hasComparablesSamples: html.includes('Pinellas, Orange, Duval'),
          hasApprovedRejectedLine: html.includes('approved 5 / rejected 1'),
          hasTopMatchesExpand: html.includes('Show 2 matching past decisions'),
          hasApprovedPill: html.includes('mkt-pill-success'),
          hasRejectedPill: html.includes('mkt-pill-danger'),
        }));
        """
    )
    assert data == {
        "hasSection": True,
        "hasScopeLabel": True,
        "hasMomentumUp": True,
        "hasMomentumDetail": True,
        "hasPriorCount": True,
        "hasSparkline": True,
        "hasComparablesCount": True,
        "hasComparablesSamples": True,
        "hasApprovedRejectedLine": True,
        "hasTopMatchesExpand": True,
        "hasApprovedPill": True,
        "hasRejectedPill": True,
    }


def test_trend_context_no_baseline_says_new() -> None:
    """delta_ratio: null and prior_window_count: 0 → 'new / no prior-period baseline'."""
    data = _run_node(
        """
        const trendContext = {
          resolved: true,
          asOf: '2026-06-04T12:00:00Z',
          theme: 'dyslexia',
          region: null,
          momentum: { window_days: 90, bucket_days: 7, buckets: [], current_window_count: 4, prior_window_count: 0, delta_ratio: null },
          comparables: { comparable_count: 0, sample_districts: [] },
          decisionHistory: { priorApproves: 0, priorRejects: 0, topMatches: [] },
        };
        const html = mod.renderTrendContextSection(trendContext);
        console.log(JSON.stringify({
          hasNoBaselineLabel: html.includes('new / no prior-period baseline'),
          hasNoDecisionsLine: html.includes('No prior decisions on similar campaigns'),
          hasNoSampleDetail: !html.includes('e.g.'),
          hasNoTopMatchesExpand: !html.includes('Show 0 matching'),
        }));
        """
    )
    assert data == {
        "hasNoBaselineLabel": True,
        "hasNoDecisionsLine": True,
        "hasNoSampleDetail": True,
        "hasNoTopMatchesExpand": True,
    }


def test_trend_context_unresolved_is_quiet() -> None:
    """resolved=false → quiet 'no trend data yet' note, no errors."""
    data = _run_node(
        """
        const html = mod.renderTrendContextSection({ resolved: false, reason: 'no_primary_signal' });
        console.log(JSON.stringify({
          hasQuietNote: html.includes('No trend data yet'),
          hasNoError: !html.includes('Error') && !html.includes('error'),
          hasNoSparkline: !html.includes('mkt-trend-sparkline'),
        }));
        """
    )
    assert data == {
        "hasQuietNote": True,
        "hasNoError": True,
        "hasNoSparkline": True,
    }


def test_trend_context_null_returns_empty_string() -> None:
    """Missing trendContext key on the proposal must not break the modal."""
    data = _run_node(
        """
        console.log(JSON.stringify({
          nullEmpty: mod.renderTrendContextSection(null) === '',
          undefinedEmpty: mod.renderTrendContextSection(undefined) === '',
        }));
        """
    )
    assert data == {"nullEmpty": True, "undefinedEmpty": True}


def test_assembled_brief_places_trend_context_before_brief_grid() -> None:
    """The campaign Brief tab should show trend context above the main brief fields."""
    data = _run_node(
        """
        const trendContext = {
          resolved: true,
          theme: 'literacy',
          region: 'FL',
          momentum: { window_days: 90, current_window_count: 9, prior_window_count: 3, delta_ratio: 3.0, buckets: [] },
          comparables: { comparable_count: 7, sample_districts: [{ name: 'Pinellas' }] },
          decisionHistory: { priorApproves: 5, priorRejects: 1, topMatches: [] },
        };
        const briefRecord = {
          assembledAt: 1780574400,
          version: 2,
          brief: {
            campaignType: { primary: 'district awareness' },
            signal: { verbatimEvidence: 'District trend moved up sharply.', urgency: { tier: 'hot' } },
            deliverables: ['email'],
            gates: ['Gate 2'],
          },
        };
        const campaign = { owner: 'Jon', rulesetVersionAtQualification: 'ruleset-v1' };
        const html = mod.renderAssembledBrief(
          briefRecord,
          campaign,
          mod.renderTrendContextSection(trendContext),
        );
        console.log(JSON.stringify({
          hasTrendContext: html.includes('mkt-trend-context'),
          hasDecisionLine: html.includes('approved 5 / rejected 1'),
          trendBeforeGrid:
            html.indexOf('mkt-trend-context') > -1 &&
            html.indexOf('mkt-brief-grid') > -1 &&
            html.indexOf('mkt-trend-context') < html.indexOf('mkt-brief-grid'),
        }));
        """
    )
    assert data == {
        "hasTrendContext": True,
        "hasDecisionLine": True,
        "trendBeforeGrid": True,
    }


# ── Piece 2: prioritization view ──────────────────────────────────────────


def test_prioritization_renders_combined_with_why_and_disclaimer() -> None:
    data = _run_node(
        """
        const payload = {
          as_of: '2026-06-04T12:00:00Z',
          window_days: 30,
          horizon_days: 60,
          state_filter: null,
          velocity_ranking: [],
          time_sensitive: [],
          combined: [
            { district_id: 101, name: 'Pinellas', state: 'FL', tier: 'tier-a', velocity_score: 12.5, velocity_rank: 1, has_time_sensitive_signal: true,  earliest_signal_created_at_iso: '2026-07-01T00:00:00Z' },
            { district_id: 102, name: 'Orange',   state: 'FL', tier: 'tier-b', velocity_score: 8.0,  velocity_rank: 2, has_time_sensitive_signal: false, earliest_signal_created_at_iso: null },
          ],
        };
        const html = mod.renderMarketingPrioritization(payload, { stateFilter: 'FL' });
        console.log(JSON.stringify({
          hasHero: html.includes('Where to focus'),
          hasWindowLabel: html.includes('Velocity (30d)'),
          hasHorizonLabel: html.includes('time-sensitivity (60d)'),
          hasTable: html.includes('mkt-prioritization-table'),
          hasDisclaimer: html.includes('Estimate') && html.includes('not a hard deadline'),
          hasPinellas: html.includes('Pinellas'),
          hasOrange: html.includes('Orange'),
          hasVelocityScore: html.includes('velocity score 12.50'),
          hasTimeSensitiveWhy: html.includes('has time-sensitive signal'),
          hasDeadlineEstimate: html.includes('est. ~'),
          hasStateSelectedFL: html.includes('value="FL" selected'),
          hasRefreshBtn: html.includes('data-prioritization-refresh'),
          hasStateControl: html.includes('data-prioritization-state'),
        }));
        """
    )
    assert data == {
        "hasHero": True,
        "hasWindowLabel": True,
        "hasHorizonLabel": True,
        "hasTable": True,
        "hasDisclaimer": True,
        "hasPinellas": True,
        "hasOrange": True,
        "hasVelocityScore": True,
        "hasTimeSensitiveWhy": True,
        "hasDeadlineEstimate": True,
        "hasStateSelectedFL": True,
        "hasRefreshBtn": True,
        "hasStateControl": True,
    }


def test_prioritization_empty_combined_shows_empty_state() -> None:
    data = _run_node(
        """
        const html = mod.renderMarketingPrioritization({
          as_of: '2026-06-04T12:00:00Z', window_days: 30, horizon_days: 60,
          state_filter: 'TX', velocity_ranking: [], time_sensitive: [], combined: [],
        }, { stateFilter: 'TX' });
        console.log(JSON.stringify({
          hasEmpty: html.includes('mkt-prioritization-empty'),
          mentionsTX: html.includes('TX'),
          hasNoTable: !html.includes('<tbody>'),
        }));
        """
    )
    assert data == {
        "hasEmpty": True,
        "mentionsTX": True,
        "hasNoTable": True,
    }


def test_signals_page_scaffold_wraps_shortlist_and_collapsible_inbox() -> None:
    data = _run_node(
        """
        const html = mod.renderMarketingSignalsPageScaffold({ inboxExpanded: true });
        console.log(JSON.stringify({
          hasSignalsHero: html.includes('mkt-hero-title">Signals</h2>'),
          hasPlaybookLink: html.includes('href="#signal-playbook"'),
          hasShortlistPanel: html.includes('data-signals-prioritization-panel'),
          hasCollapsibleInbox: html.includes('data-signals-collapsible') && html.includes('data-signals-inbox-panel'),
          defaultsOpen: html.includes('data-signals-collapsible open'),
          hasToggleLabel: html.includes('Show all signals'),
        }));
        """
    )
    assert data == {
        "hasSignalsHero": True,
        "hasPlaybookLink": True,
        "hasShortlistPanel": True,
        "hasCollapsibleInbox": True,
        "defaultsOpen": True,
        "hasToggleLabel": True,
    }


# ── Static wiring + integration points ────────────────────────────────────


def test_navigation_aliases_legacy_signals_routes_to_unified_view() -> None:
    script = f"""
      const nav = await import({json.dumps(NAV_JS.as_uri())});
      console.log(JSON.stringify({{
        signals: nav.normalizeAppView('marketing-signals'),
        prioritizationAlias: nav.normalizeAppView('marketing-prioritization'),
        whereAlias: nav.normalizeAppView('where-to-focus'),
        inboxAlias: nav.normalizeAppView('signals-inbox'),
      }}));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        text=True,
        capture_output=True,
    )
    data = json.loads(result.stdout)
    assert data == {
        "signals": "marketing-signals",
        "prioritizationAlias": "marketing-signals",
        "whereAlias": "marketing-signals",
        "inboxAlias": "marketing-signals",
    }


def test_navigation_moves_playbook_out_of_marketing_and_renames_signals_nav() -> None:
    nav = NAV_JS.read_text()
    index_html = (ROOT / "public/index.html").read_text()
    assert 'label: "Signals"' in nav
    assert 'label: "Signals Inbox"' not in nav
    assert 'label: "Where to focus"' not in nav
    assert 'section: "Settings"' in nav
    assert ">Signals</span>" in index_html
    assert ">Signal Playbook</span>" not in index_html
    assert ">Where to focus</span>" not in index_html


def test_api_exports_prioritization_fetch() -> None:
    api = API_JS.read_text()
    assert "export async function fetchMarketingPrioritizationApi" in api
    assert "/api/marketing/intel/prioritization" in api


def test_home_routes_prioritization() -> None:
    home = HOME_JS.read_text()
    assert "MARKETING_PRIORITIZATION_VIEW" in home
    assert "loadMarketingPrioritization" in home


def test_marketing_module_exports_render_helpers() -> None:
    js = MKT_JS.read_text()
    css = MKT_CSS.read_text()
    assert "export function renderTrendContextSection" in js
    assert "export function renderMarketingSignalsPageScaffold" in js
    assert "export function renderMarketingPrioritization" in js
    assert "export async function loadMarketingPrioritization" in js
    assert 'href="#signal-playbook"' in js
    assert "loadMarketingSignalsInboxPanel" in js
    assert "renderTrendContextSection(bundle?.trendContext)" in js, (
        "trend block must be injected into the initiation modal"
    )
    assert "const _briefTrendContextCache = new Map();" in js
    assert (
        "return _renderLegacyBriefFields(c, trendContextSection + assembleSection + rulesetRow);"
        in js
    )
    assert "${trendContextSection}" in js
    assert "getCampaignInitiationProposalApi(campaign.id)" in js
    assert "_shouldLoadBriefTabData(campaign.id)" in js
    assert ".mkt-trend-context" in css
    assert ".mkt-signals-page-collapsible" in css
    assert ".mkt-prioritization-table" in css
    assert ".mkt-prioritization-disclaimer" in css
