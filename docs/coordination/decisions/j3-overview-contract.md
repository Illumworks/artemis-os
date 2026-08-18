# J3 Overview Contract — Frontend ↔ Backend Shape Agreement

**Author:** Worker J3 (2026-05-17)
**Purpose:** Single source of truth for the JSON shapes each missing aggregator must return. Sub-agents J3a/J3b/J3c read this before writing backend code.

---

## Context

`home.js` calls these routes on page-load. All 404 right now. The fix:

1. `GET /api/okr/overview` — J3a
2. `GET /api/calendar/overview` — J3b
3. `GET /api/meetings/overview` — J3b
4. `GET /api/jira/overview` — J3c
5. `GET /api/sessions` — J3c stub
6. `GET /api/stats/analytics` — J3c stub
7. `GET /api/stats/providers` — J3c real
8. `GET /api/notifications/history` — J3c stub

---

## 1. `GET /api/okr/overview`

### Success response (has data)
```json
{
  "status": "ok",
  "objectives": [
    {
      "id": 1,
      "title": "Grow Amira's school district pipeline",
      "progress": 62,
      "tone": "sage",
      "cycle": "Q2-2026",
      "krs": [
        {
          "id": 7,
          "title": "Land 3 district pilots",
          "prog": 33,
          "status": "ontrack",
          "note": null,
          "evidence_count": 2,
          "done_bullets": [],
          "gaps_bullets": ["Waiting on Fresno USD legal"],
          "target_text": "3 pilots"
        }
      ]
    }
  ],
  "stats": [
    { "n": 3,  "suffix": "", "tone": "sage", "label": "Objectives" },
    { "n": 11, "suffix": "", "tone": "sage", "label": "Key Results" },
    { "n": 2,  "suffix": "", "tone": "warn", "label": "At risk" }
  ],
  "activity": [
    {
      "id": 42,
      "text": "Added district pilot evidence",
      "kr_id": 7,
      "kr_label": "Land 3 district pilots",
      "created_at": "2026-05-16T14:30:00Z"
    }
  ],
  "evidence": [],
  "next_up": [
    { "id": 1, "ref": "OKR", "text": "Follow up Fresno USD legal", "prio": "high" }
  ],
  "nextUp": [
    { "id": 1, "ref": "OKR", "text": "Follow up Fresno USD legal", "prio": "high" }
  ],
  "quarter": { "label": "Q2 2026" }
}
```

**Notes:**
- `krs` (NOT `keyResults`) — the frontend accesses `objective.krs || []`.
- `evidence_count` per KR — computed as count of `okr_activity` rows with `kr_id = kr.id`.
- Include `nextUp` AND `next_up` (alias) — frontend uses camelCase `nextUp`.
- Exclude `archived_at IS NOT NULL` objectives and KRs by default.
- `quarter.label` format: `"Q2 2026"` (no dash). Derive from current date or from `cycle` field.
- `stats[].tone` options: `"sage"` (normal), `"warn"` (at-risk), `"zero"` (nothing).
- "At risk" count = KRs with `status = 'atrisk'`.
- `activity` — most recent 10 entries, newest first.

### Empty response (no objectives yet)
```json
{
  "status": "ok",
  "objectives": [],
  "stats": [
    { "n": 0, "suffix": "", "tone": "zero", "label": "Objectives" },
    { "n": 0, "suffix": "", "tone": "zero", "label": "Key Results" },
    { "n": 0, "suffix": "", "tone": "zero", "label": "At risk" }
  ],
  "activity": [],
  "evidence": [],
  "next_up": [],
  "nextUp": [],
  "quarter": { "label": "Q2 2026" }
}
```

---

## 2. `GET /api/calendar/overview`

### Not connected (default state)
```json
{ "status": "not_connected", "provider": "gcal" }
```

### Connected
```json
{
  "status": "ready",
  "today": {
    "meetingsCount": 3
  },
  "nextEvent": {
    "startLabel": "2:00 PM",
    "title": "Weekly team sync"
  }
}
```

**How to detect connected:** `repo.list_active(session, provider="gcal")` returns a non-empty list.

**nextEvent:** first event from today onwards from `gcal_events_cache` or a live GCal fetch. If no event today, return `null`.

**Connected detection matters** for `loadCalendarShell`:
```js
if (!calendarOverview || calendarOverview.status !== 'ready') {
  // show "Connect Google Calendar in /integrations" view
}
```

---

## 3. `GET /api/meetings/overview`

Always returns not-connected until J5 (Granola integration):
```json
{ "status": "not_connected", "provider": "granola" }
```

Frontend `loadMeetingsShell` checks:
```js
const granolaConnected = granolaOverview?.connected === true;
```
(via `fetchGranolaMeetingsApi` which is a different stub)

`fetchMeetingsOverviewApi` just needs to not 404 — returning not_connected is correct and handled gracefully.

---

## 4. `GET /api/jira/overview`

Always returns not-connected until J4 (Jira integration):
```json
{ "status": "not_connected", "provider": "jira" }
```

Frontend `buildJiraDedicatedViewModel` checks:
```js
if (!jiraOverview || !jiraOverview.connected) {
  return { badge: 'Needs setup', statusTone: 'setup', ... };
}
```
So `connected` absent (or false) → "Needs setup" render — correct behavior.

---

## 5. `GET /api/sessions`

Stub — sessions concept maps to `fa_sessions` but the frontend expects
the Node app's session model (`{id, title, last_used_at, provider_id, mode, ...}`).
V1: return empty list.

```json
[]
```

---

## 6. `GET /api/stats/analytics`

Stub for now. Frontend reads `analytics?.overview?.sessions` for session count.
Return a safe-default shape that doesn't break any consumer.

```json
{
  "overview": {
    "sessions": 0,
    "messages": 0,
    "tokens": 0
  },
  "tokens_today": 0,
  "cost_today_usd": 0.0,
  "runs_today": 0
}
```

Router prefix: `GET /api/stats/analytics` (matches `fetchAnalytics` in api.js).

---

## 7. `GET /api/stats/providers`

Real data. Returns configured/healthy status for each registered LLM provider.

```json
[
  { "provider_id": "anthropic", "name": "Anthropic", "configured": true,  "healthy": null },
  { "provider_id": "gemini",    "name": "Gemini",    "configured": false, "healthy": null },
  { "provider_id": "openrouter","name": "OpenRouter","configured": false, "healthy": null }
]
```

**Implementation:** call `list_providers()` from `artemis.providers`, then for each,
attempt `get_provider_config(session, provider_id)` — if it returns a non-empty dict, `configured=True`.
`healthy=null` always (no ping in V1; a healthcheck cron is future work).

Route: `GET /api/stats/providers` — add to a new `artemis/routes/stats.py` file.

---

## 8. `GET /api/notifications/history`

Stub. There is no `notifications` table in the current schema.
Return empty list with the shape the frontend can handle.

```json
[]
```

Frontend `fetchNotificationHistory` returns the raw array and callers do:
```js
notifications.filter((item) => !item?.read_at).length
```
So `[]` is the safe stub.

Route: `GET /api/notifications/history` — query params `limit`, `offset`, `unread_only`, `type` (all ignored in V1 stub).

---

## Route prefix / router wiring summary

| Route                       | New file                          | Register in main.py as              |
|-----------------------------|-----------------------------------|--------------------------------------|
| GET /api/okr/overview        | `artemis/routes/okr.py` (add endpoint) | already registered `okr.router`   |
| GET /api/calendar/overview   | `artemis/routes/calendar.py`      | `from artemis.routes import calendar; app.include_router(calendar.router)` |
| GET /api/meetings/overview   | `artemis/routes/meetings.py`      | same pattern                         |
| GET /api/jira/overview       | `artemis/routes/jira.py`          | same pattern                         |
| GET /api/sessions            | `artemis/routes/sessions.py`      | same pattern                         |
| GET /api/stats/analytics     | `artemis/routes/stats.py`         | same pattern                         |
| GET /api/stats/providers     | `artemis/routes/stats.py`         | same as above (one router, two routes) |
| GET /api/notifications/history | `artemis/routes/notifications.py` | same pattern                       |

**Auth:** All routes must use `Depends(require_token)` from `artemis.marketing.routes._auth`.

---

## Test count targets

- J3a: 6 tests (overview endpoint — empty DB, with data, archived filtered)
- J3b: 8 tests (calendar not_connected, connected; meetings always not_connected)
- J3c: 6 tests (jira not_connected, sessions stub, analytics stub, notifications stub, stats/providers shape)
