# Scout Team — Detect

Nine scout agents detect signals from public records and emit them to the Signal Queue. All nine emit the same Signal schema; all nine read from the same Territory Config.

## Roster

| ID | Agent | Status | Primary sources |
|---|---|---|---|
| 1.1 | Starbridge Researcher | Bench-test (kept active during 6–12 mo eval) | Starbridge API |
| 1.2 | Regional News Scout | Active | News APIs, state DoE, board minutes, supe transitions |
| 1.3 | LinkedIn Observer (Mode B only) | Active | LinkedIn public posts; Mode A disabled in v1 |
| 1.4 | Legislative Scout | Active | LegiScan API |
| 1.5 | Federal Funding Scout | Active | Federal Register, Grants.gov, ED.gov |
| 1.6 | State DoE Scout | Active | Per-state DoE sites + governor RSS |
| 1.7 | Procurement Scout | Active (net-new capability) | Statewide procurement portals + watch-list district portals |
| 1.8 | Board Minutes Scout | Active | BoardDocs, Granicus, district sites |
| 1.9 | Leadership Transition Scout | Active | Cross-source: board, press, state DoE, news, LinkedIn |

## Build order

Build 1.4 first — easiest scout, validates the pattern end-to-end against the simplest API (LegiScan). Then 1.5 (Federal Funding) to validate the pattern scales. STOP and run end-to-end through Cross-Reference Agent before building the rest. This catches integration issues before they're spread across 9 scouts.

After 1.4 + 1.5 prove out:
1. 1.6 State DoE Scout (shares scraping pattern with later scouts)
2. 1.9 Leadership Transition Scout (cross-source, validates multi-source dedupe)
3. 1.7 Procurement Scout
4. 1.8 Board Minutes Scout
5. 1.2 Regional News Scout
6. 1.3 LinkedIn Observer (Mode B only)
7. 1.1 Starbridge Researcher (last — depends on Starbridge API availability and credit allocation from Kristen / Angela)

## What every scout does

Every scout file follows the same template:

- **Purpose** — what this scout exists to do
- **Cadence** — when it runs
- **Inputs** — APIs, configs, memory
- **Outputs** — signals emitted, with which `discovered_by` value
- **Tools required** — function signatures the scout needs
- **Prompt scaffolding** — the system prompt (where LLM is used)
- **Failure modes** — what to log, what to retry, what to escalate
- **DB tables touched** — for code review and audit
- **Implementation notes for Codex** — concrete Python guidance

## What no scout does

- No outreach. No emails sent. No drafts written.
- No deciding whether a campaign should launch. That's the Qualifier's job.
- No inventing reason codes. Codes must come from `reason_code_registry`.
- No paraphrasing source content. `verbatim_snippet` is exact.
- No modifying any other scout's signals. Read-only across scouts.
- No writing to `contacts` table (Contact team is out of scope). Scouts may populate `contact_hints` on their own signals only.

## Handoff to Qualifier

Scouts write to `signal_queue` with status `pending_qualification`. The Qualifier polls every 5 minutes. Scouts do not call the Qualifier directly. Decoupled by the queue.
