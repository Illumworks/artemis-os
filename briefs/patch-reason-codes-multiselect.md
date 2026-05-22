# Patch — Reason Codes Multi-Select Dropdown Empty

**Owner:** Codex (paste-ready, diagnose-then-fix)
**Branch:** `codex/patch-reason-codes-multiselect`
**LOC budget:** ~100 (honest overrun OK to ~160)
**Depends on:** Codex 4 (agent-reason-codes-injection) merged.

## Why

Codex 4's brief shipped a "registry-backed multi-select editor that PATCHes inline." Jon's walkthrough reports the section looks like a "free text form field" and "is empty" — no codes selectable, no dropdown options visible.

The brief was clear: multi-select sourced from `/api/signal-criteria/reason-codes`. Something between API and frontend rendering is broken.

## Scope

### Step 1: Diagnose

```bash
pwd && git branch --show-current
# must be: artemis-os on lead/j6a-granola-integration

# 1. Verify registry has codes
curl -sS "http://127.0.0.1:8000/api/signal-criteria/reason-codes" | python3 -m json.tool

# Possible outcomes:
# (a) Returns [...17 codes...]  → API fine, frontend broken
# (b) Returns []                → Registry not seeded; run seeder
# (c) Returns 404               → Route doesn't exist; routing bug
# (d) Returns 500               → Backend bug

# If (b): seed the registry
ls scripts/ | grep -i "reason\|signal"
# Find the M1 seed loader. If it exists:
uv run python scripts/<seed_loader_name>.py

# If (c) or (d): trace the route to find the bug
grep -rn "signal-criteria\|reason-codes" artemis/routes/ artemis/marketing/

# 2. Verify the Codex 4 frontend component
grep -rn "reason_codes_emitted\|reason-codes-multiselect\|reasonCodesEmitted" public/js/

# Identify which file renders the field. Examine:
# - Does it fetch /api/signal-criteria/reason-codes on render?
# - Does it render the response as a dropdown OR as a text input?
# - Is there a CSS rule hiding the dropdown options?

# 3. Browser test
# Open Agents page → click any marketing scout → DevTools Network tab → look for the fetch
# Confirm: does the fetch fire? Does it return data? Is the response data rendered?
```

### Step 2: Fix

Based on diagnostic results:

**If registry is empty (case b):** run the M1 seed loader. Document that the M1 seeder should be in the deployment runbook (lives in docs/MARKETING-PIPELINE-CANONICAL.md or similar).

**If registry is populated but UI doesn't show dropdown (case a):**

Most likely the multi-select component fetches but renders text instead of options. Patch the component:

```javascript
async function renderReasonCodesEditor(agent) {
  const allCodes = await fetch('/api/signal-criteria/reason-codes').then(r => r.json());
  const selected = new Set(agent.reason_codes_emitted || []);

  const dropdown = document.createElement('div');
  dropdown.className = 'multiselect-dropdown';

  // Render each code as a checkbox / chip
  for (const code of allCodes) {
    const chip = document.createElement('label');
    chip.className = 'multiselect-chip';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.value = code.code;
    cb.checked = selected.has(code.code);
    cb.addEventListener('change', () => saveReasonCodes(agent.id));
    chip.appendChild(cb);
    chip.appendChild(document.createTextNode(code.code));
    dropdown.appendChild(chip);
  }

  return dropdown;
}
```

(Adapt to whatever pattern Codex 4 used. Don't rewrite from scratch if there's existing structure that's close.)

**If route returns 404 (case c) or 500 (case d):** real backend bug; fix the route handler.

### Step 3: Verify

- Click any agent in Agents page
- Reason codes section shows multi-select with 17 codes visible
- Selected codes (per `agent.reason_codes_emitted`) are pre-checked
- Click a checkbox → PATCH fires → success toast → refresh → state persisted

### Tests

- Backend: `/api/signal-criteria/reason-codes` returns 17 codes (matches Josh's spec)
- Frontend: multi-select renders all codes from API; selection state persists; PATCH on change

## Out of scope

- Adding new reason codes via UI. v1 only edits which codes an agent emits; full code registry CRUD is a future brief.
- Search/filter within the dropdown if it grows. 17 codes is fine without search.
- Color coding by domain prefix (POLICY_*, FUNDING_*, etc.). Polish later.

## Files expected (depends on diagnostic)

If frontend fix:
| File | LOC |
|---|---|
| `public/js/features/operations-shell.js` or the Agent Card component | ~40 delta |
| `public/css/features/operations.css` | ~20 delta (chip styling if missing) |
| `tests/` | ~30 |

**Total: ~90 LOC** if frontend-only. **Add ~50 LOC** if route fix needed.

## Invariants

- node --check on modified JS
- conftest hard-fail on non-test DB
- git switch lead/j6a-granola-integration after commit
- Don't add Worker-time fixed code list — read from the registry table

## Report

git diff --stat, root cause (which of a/b/c/d), screenshot of working multi-select with 17 codes visible, click-to-toggle persistence verified, test pass count, branch.

---

**Lead notes (not for Codex):**
- Verify the registry is seeded BEFORE assuming it's a frontend bug. The seed should have been run as part of M1's merge but may have been missed in production. Stale-runtime patterns this session strongly suggest data-layer drift is common.
- After this lands + Jon walks again, the reason codes editor closes the loop on Codex 4's promise.
