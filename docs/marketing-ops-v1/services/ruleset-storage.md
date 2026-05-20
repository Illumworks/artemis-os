# Service — Ruleset Storage

Append-only versioned storage for rulesets. Every edit creates a new version.

**Backing table:** `ruleset_versions`
**Module path:** `artemis_os/services/ruleset_storage.py`

## Interface

```python
from typing import Optional
from artemis_os.schemas.ruleset import Ruleset, CompiledRuleset

def get_active(ruleset_id: str) -> Optional[CompiledRuleset]:
    """Return the currently active version of a ruleset."""

def get_version(ruleset_id: str, version: int) -> Optional[CompiledRuleset]:
    """Return a specific version (used for in-flight campaigns referencing older versions)."""

def list_active() -> list[CompiledRuleset]:
    """Return all currently active rulesets across all campaign types."""

def write_new_version(
    ruleset_id: str,
    yaml_source: str,
    compiled: CompiledRuleset,
    author: str,
) -> int:
    """
    Append a new version. Does NOT activate.
    Returns the new version number.
    """

def activate(ruleset_id: str, version: int, approved_by: str) -> None:
    """
    Transactionally:
      - Set old active version (if any) to is_active = false
      - Set this version to is_active = true
    Raises VersionNotFoundError if the version doesn't exist.
    Raises NotApprovedError if approved_by is not authorized.
    """

def get_hit_rate(ruleset_id: str, version: int) -> Optional[float]:
    """
    Returns the proportion of signals that triggered this ruleset at score > 0.7,
    aggregated over the last 30 days. Updated by a background job.
    """
```

## Versioning invariants

- One row per `(ruleset_id, version)`. Composite primary key enforces this.
- `is_active = TRUE` is permitted on at most one row per `ruleset_id`. Enforced via partial unique index.
- `version` is monotonically increasing per `ruleset_id`. Compiler assigns next available integer.
- No deletes. No updates to existing rows except the `is_active` toggle and `hit_rate` background updates.

## Activation transaction

```sql
BEGIN;
  UPDATE ruleset_versions SET is_active = FALSE WHERE ruleset_id = :id AND is_active = TRUE;
  UPDATE ruleset_versions SET is_active = TRUE, approved_by = :user, approved_at = NOW()
    WHERE ruleset_id = :id AND version = :version;
COMMIT;
```

Wrap in a single transaction. If either UPDATE fails, the whole transaction rolls back.

## Initial seed

At DB init, after Ruleset Compiler can run, seed three rulesets at version 1:

- `obc` (from `rulesets/obc.yaml`)
- `biliteracy` (from `rulesets/biliteracy.yaml`)
- `dyslexia` (from `rulesets/dyslexia.yaml`)

All seeded with `is_active = TRUE`, `approved_by = 'seed'`. Josh will replace these via Ruleset Manager Agent over time.
