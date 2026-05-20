# Service — Territory Config

Single source of truth for the priority states, watch keywords, deprioritized lists, and watch-list districts that drive all scouts and hard filters.

**Backing table:** `territory_config` (single-row), `districts.is_watch_list` (per-district flag)
**Module path:** `artemis_os/services/territory_config.py`

## Why this is shared infrastructure

From the canvas architectural reasoning: agents do NOT have state-specific code. Territory lives in shared config; agents read from it. State logic lives in the watch list, not in the agent.

This means:
- Cross-state patterns (FL → IN → TX OBC trend) are visible because every scout reads the same config.
- Strategic shifts (adding Ohio to priority states) take a config update, not engineering.
- Cost scales with watch-list size, not with agent count.

## Interface

```python
def get_priority_states() -> list[str]:
    """Returns the active priority state list (two-letter codes)."""

def get_watch_keywords(campaign_type: str) -> list[str]:
    """Returns the keyword list for a campaign type (e.g., 'OBC', 'dyslexia', 'biliteracy')."""

def get_deprioritized() -> list[dict]:
    """
    Returns the list of deprioritized filters.
    Example: [{"campaign_type": "biliteracy", "state": "TX"}] means
    do not emit biliteracy signals for Texas districts.
    """

def get_watch_list() -> list[str]:
    """Returns district_id list for the 200–500 priority districts under deep monitoring."""

def is_watch_list(district_id: str) -> bool:
    """Boolean check used by scouts to decide between weekly and daily cadence for a district."""

def update(
    priority_states: Optional[list[str]] = None,
    watch_keywords: Optional[dict] = None,
    deprioritized: Optional[list[dict]] = None,
    updated_by: str = "system",
) -> None:
    """Update config. Writes to the single-row territory_config table with audit fields."""
```

## Default seed values

From the canvas:

```python
priority_states = ["FL", "IN", "MD", "MO", "MI", "IL", "TX"]
watch_keywords = {
    "OBC": [],          # populated by Josh via Rulesets surface
    "dyslexia": [],
    "biliteracy": [],
}
deprioritized = []
```

`// TODO: confirm exact priority_states list with Kristen / Angela before MVP-3 ship.`

## Watch-list construction

The watch list of 200–500 districts is derived (not manually maintained for v1) by this rule:

```sql
UPDATE districts SET is_watch_list = TRUE
WHERE state = ANY(:priority_states)
  AND enrollment >= 5000
  AND (
    -- Districts with prior Artemis activity
    EXISTS (SELECT 1 FROM signal_queue WHERE signal->'geography'->>'district_id' = districts.district_id)
    -- OR top N by enrollment per state
    OR districts.district_id IN (
      SELECT district_id FROM (
        SELECT district_id, ROW_NUMBER() OVER (PARTITION BY state ORDER BY enrollment DESC) as rn
        FROM districts WHERE state = ANY(:priority_states)
      ) sub WHERE rn <= 50
    )
  );
```

This is a v1 heuristic. Josh and Angela can override at any time by directly setting `is_watch_list` on `districts` rows.

`// JUDGMENT CALL:` "top 50 by enrollment per state" is arbitrary. Tune after first month of signal volume.

## Federal / state-level signals (special case)

Some signals are not district-specific (federal grant announcements, state-wide mandates). For these:

- `signal.geography.district_id` is set to a synthetic state-level ID: `STATE_<two_letter>` (e.g., `STATE_FL`).
- These synthetic IDs exist in the `districts` table with `is_watch_list = TRUE` for priority states.
- Contact DB stub returns `True` for these (they're always in scope).
- Cross-Reference Agent Phase 3 may fan out a single state-level signal into per-district signals if the ruleset's routing logic supports it.
