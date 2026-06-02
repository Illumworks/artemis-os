# Service — Contact DB Stub

**v1 placeholder.** The Contact team is out of scope for v1 (per PM direction). This stub satisfies the hard filter check in Qualifier Phase 1 without forcing the team to ship contact enrichment.

**Future:** Replace this stub with the real Contact DB when Contact team work is unblocked.

**Module path:** `artemis_os/services/contact_db_stub.py`

## Interface

```python
def has_contact(district_id: str) -> bool:
    """
    Returns True if Artemis has at least one contact for the district.

    v1 behavior: Returns True for any district where districts.is_watch_list = TRUE
    OR districts.state IN territory_config.priority_states.
    Returns False otherwise.

    Why: We do not want to hard-filter out signals for priority districts
    just because no contact has been enriched yet. Humans can find a contact
    at Gate 1 review time. We DO want to filter out signals for non-priority
    districts that have no contact pathway.
    """

def get_contacts(district_id: str) -> list[dict]:
    """
    v1 behavior: Returns empty list. Contact enrichment is out of scope.
    Brief Composer can still surface contact_hints from the originating signal.

    Future: Returns list of contact records with name, role, email, linkedin_url.
    """
```

## Implementation

```python
# artemis_os/services/contact_db_stub.py

from artemis_os.services.territory_config import get_priority_states
from artemis_os.db import get_district

def has_contact(district_id: str) -> bool:
    district = get_district(district_id)
    if not district:
        return False
    if district.is_watch_list:
        return True
    if district.state in get_priority_states():
        return True
    return False

def get_contacts(district_id: str) -> list[dict]:
    return []  # v1 stub — Contact team out of scope
```

## When to replace this stub

When the Contact team ships:

1. Add a `contacts` table to the database.
2. Replace `contact_db_stub.py` with `contact_db.py` implementing the real interface.
3. Update Qualifier Phase 1 hard filter to use richer contact data (e.g., role-level matching).
4. Update Brief Composer to display real enriched contacts instead of just contact_hints.
5. Update Campaign Brief Assembler (5.1) to populate `target_contacts` with real records.

The interface (`has_contact`, `get_contacts`) is intentionally minimal so the real implementation can extend it without breaking callers. Anything calling this service should depend on the function signature, not internals.

## Tests for v1

- `has_contact('FL_pinellas')` returns `True` (Pinellas is in priority state FL).
- `has_contact('CA_los_angeles')` returns `False` (CA not in priority states by default).
- `has_contact('FL_pinellas')` after setting `is_watch_list = TRUE` still returns `True`.
- `has_contact('not_a_real_district')` returns `False` (no district row).
- `get_contacts(any_district_id)` returns `[]`.
