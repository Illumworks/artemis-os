# AUDIT BRIEF — Operations section (Py vs Node gap report)

**Role:** You are Codex running an **AUDIT ONLY**. Do not modify any code. Your deliverable is one markdown report.

**Why this brief exists:** Artemis is being rebuilt in Python (FastAPI/SQLAlchemy/Postgres) from a frozen Node reference. The Lead+Worker pair has walked the user-facing rail pages (Calendar, Slack, OKR Studio, Jira Board, Meetings). The **Operations section** (Automations, Skills, Agents, Workflows, Memory) is the next slab to walk. The user reports that **Agents is stuck on a loading spinner** even though we supposedly shipped the agent system — suggests the surface isn't actually wired through. We need to know exactly what's missing before we touch it.

## The two worktrees

| | Path | What it is |
|---|---|---|
| **Node reference** (frozen, do not modify) | `/Users/artemis/Desktop/Artemis/claudeck-artemis/` | The original working Node implementation. Browse it for ground truth on shape, behavior, data, endpoints. |
| **Python rebuild** (audit target) | `/Users/artemis/Desktop/Artemis/artemis-os/` | The active Python rebuild. Find what's there, what's broken, what's missing. |

## Scope — five surfaces

Audit each of these. The frontend file `public/js/features/home.js` in both repos contains the page-loading code (look for `loadAutomationsShell`, `loadSkillsShell`, `loadAgentsShell`, `loadWorkflowsShell`, `loadMemoryShell`).

1. **Automations** — `'automations'` view
2. **Skills** — `'skills'` view
3. **Agents** — `'agents'` view (known broken: stuck loading)
4. **Workflows** — `'workflows'` view
5. **Memory** — `MEMORY_VIEW`

## What to check for each surface

For each of the five, produce a section in the report covering:

### A. Frontend
- Where is the page shell rendered? (e.g. `loadAgentsShell` in `home.js` or a separate module)
- What API endpoints does it call? List every `fetch()` call inside the shell loader and its descendants. Use grep aggressively.
- What state/data does it expect? (response shape it tries to read)
- Any Web Components specific to this surface (in `public/js/components/`)
- Run **diff at the shape level**: do the JS files exist in both repos? Where is the Python frontend reading from a different endpoint or expecting a different shape?

### B. Backend
- Which Python route module handles it? (`artemis/routes/<thing>.py`)
- Does that route module exist? If yes, list every endpoint and its return shape. If no, mark it MISSING and note what Node had.
- Does the Python module return the shape the frontend expects? If not, what's the diff?
- Database tables involved? Are they populated, or empty?

### C. The "stuck loading" question (Agents specifically)
For Agents particularly, trace the loading hang: open DevTools → Network → click Agents → which request hangs/404s/never resolves? Document the exact request path, status code, and response body. If the network call succeeds but the frontend doesn't render, find the render gate (which response field does the renderer check that's missing).

### D. Compared to Node
- What does the Node `claudeck-artemis/` repo do for this surface that we don't yet? (Be brief — a few bullets, not exhaustive.)
- Key behaviors / data flows in Node that **MUST** be preserved in Python (write these down — they become acceptance criteria when we rebuild).

### E. Gap summary
Three buckets per surface:
- **Working** — what already functions
- **Broken** — what's there but doesn't work (with the specific failure mode)
- **Missing** — what Node has and Python doesn't yet

### F. Suggested divvy
For each surface, recommend whether the rebuild should be:
- **Lead** (architecturally novel / cross-cutting / needs decisions)
- **Worker** (mechanical port from Node — has a clear contract)
- **Codex** (greenfield / self-contained / well-specified)

Estimate LOC scope per surface (rough — 100 / 300 / 500 / 1000+).

## Tools to use

```bash
# Find route modules
ls /Users/artemis/Desktop/Artemis/artemis-os/artemis/routes/
ls /Users/artemis/Desktop/Artemis/claudeck-artemis/server/

# Find frontend shell loaders
grep -n "loadAutomationsShell\|loadSkillsShell\|loadAgentsShell\|loadWorkflowsShell\|loadMemoryShell" \
    /Users/artemis/Desktop/Artemis/artemis-os/public/js/features/home.js \
    /Users/artemis/Desktop/Artemis/claudeck-artemis/public/js/features/home.js

# Find API endpoint calls inside a function (replace function name)
sed -n '/^async function loadAgentsShell/,/^}$/p' \
    /Users/artemis/Desktop/Artemis/artemis-os/public/js/features/home.js

# Compare directory contents
diff <(ls /Users/artemis/Desktop/Artemis/artemis-os/public/js/features/) \
     <(ls /Users/artemis/Desktop/Artemis/claudeck-artemis/public/js/features/)

# Check if a route exists in Python
grep -rn "^router.get.*agents\|^router.post.*agents" /Users/artemis/Desktop/Artemis/artemis-os/artemis/routes/

# DB table population (running app on :8000)
curl -s http://localhost:8000/api/agents 2>&1 | head -c 500
```

## What you must NOT do

- Do not modify any file in either worktree
- Do not run `git` operations that change state
- Do not run alembic migrations, tests, or scripts that touch the database
- Do not "fix" anything you find — your job is to map the territory, not change it

## Deliverable

A single markdown file at `/Users/artemis/Desktop/Artemis/artemis-os/audits/operations-section-gap-report.md`.

Structure:

```markdown
# Operations Section — Gap Report (Py vs Node)

Generated: <date>
Auditor: Codex

## TL;DR (one paragraph)
<5 sentences: what state is the Operations slab in overall, what's the biggest blocker, what's the smallest win we can ship to unblock it.>

## Per-surface audits

### 1. Automations
[A. Frontend / B. Backend / C. n/a / D. vs Node / E. Gap summary / F. Suggested divvy]

### 2. Skills
[…]

### 3. Agents ← KNOWN BROKEN
[Include the stuck-loading trace verbatim from DevTools or curl]

### 4. Workflows
[…]

### 5. Memory
[…]

## Cross-cutting observations
- Patterns common across surfaces (e.g. "all five expect a paginated envelope `{items, cursor}` but Python returns flat arrays")
- Shared infrastructure missing (e.g. an `agents` table that other surfaces depend on)
- Risk callouts

## Recommended sequencing
A short ordered list: which surface to attack first and why. Prefer wins that unblock multiple surfaces (e.g. if Memory backs Agents context, do Memory first).

## Estimated total effort
LOC count + Lead/Worker/Codex split + half-day count.
```

## Quality bar before you report done

- [ ] Every surface covered with all six subsections (A-F)
- [ ] The Agents "stuck loading" failure mode documented with the exact failing request
- [ ] Every claim about "Python doesn't have X" verified by an actual file check, not assumed
- [ ] Recommendations are **actionable** — "investigate further" doesn't count; say what to do
- [ ] Report fits in one focused read (under ~800 lines)

## Where to start

1. Read this brief twice
2. List both repos' routes/features directories side-by-side
3. Start with **Agents** (known broken — it'll teach you the diagnostic pattern that applies to the others)
4. Then go top-down through the sidebar (Automations → Skills → Agents → Workflows → Memory)
