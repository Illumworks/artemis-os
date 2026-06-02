# Qualifier Team — Qualify

Four components convert raw signals into Josh-readable inbox cards. The signal flows: Cross-Reference Agent (3 phases) → Brief Composer Agent → Signals Inbox. Ruleset Manager and Ruleset Compiler are out-of-band — they exist to manage the rule logic Cross-Reference Agent uses, not in the signal flow.

## Roster

| ID | Component | Type | Role |
|---|---|---|---|
| 2.1 | Cross-Reference Agent | Agent (mixed deterministic + LLM) | Score and route each signal |
| 2.2 | Ruleset Manager Agent | Agent (LLM, on-demand) | Edit rulesets via Josh's chat panel |
| 2.3 | Ruleset Compiler | Deterministic | Convert YAML → executable runtime |
| 2.4 | Brief Composer Agent | Agent (LLM) | Convert scored signal → inbox card |

Plus:
- **Storage:** Ruleset Versioning (`ruleset_versions` table; service in `services/ruleset-storage.md`)
- **Surface:** Josh's chat panel (UI; reference only in this spec)
- **Surface:** Signals Inbox (Gate 1; see `gates/gate-1-signals-inbox.md`)

## Build order

1. **2.3 Ruleset Compiler** — pure logic, deterministic, no upstream dependencies. Easiest to test.
2. **2.1 Cross-Reference Agent** — depends on compiler output. Build with seed rulesets (`rulesets/obc.yaml`, etc.) so it has something to evaluate against.
3. **2.4 Brief Composer Agent** — needs 2.1 output to compose against.
4. **2.2 Ruleset Manager Agent** — built last; depends on the rest being in place to make changes meaningful.

## End-to-end flow

```
signal_queue (status: pending_qualification)
     │
     ▼
2.1 Cross-Reference Agent
     ├── Phase 1: hard filters (deterministic, DB queries)
     │   └── fail → signal status = rejected_hard_filter
     ├── Phase 2: score against ALL rulesets
     │     - weighted_signals: deterministic checks
     │     - qualitative_rubrics: LLM calls (1 per rubric per ruleset)
     │   └── all scores < 0.4 → signal status = rejected_low_fit
     └── Phase 3: route to top campaign type(s)
         └── primary score >= 0.7 → signal status = qualified
     │
     ▼
2.4 Brief Composer Agent
     └── writes signal_briefs row, signal status = brief_composed → pending_human_review
     │
     ▼
Signals Inbox (Gate 1)
```

The Ruleset Manager Agent and Ruleset Compiler operate out-of-band:

```
Josh's chat panel
     │
     ▼
2.2 Ruleset Manager Agent
     └── writes new ruleset YAML
         │
         ▼
2.3 Ruleset Compiler
     └── compiles YAML → CompiledRuleset, writes to ruleset_versions (is_active=false)
         │
         ▼
Josh approves
     └── activate(): sets is_active=true; in-flight campaigns keep old version
```
