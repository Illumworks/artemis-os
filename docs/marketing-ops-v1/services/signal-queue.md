# Service — Signal Queue

PostgreSQL-backed append-only queue. The contract between scouts (writers) and Qualifier (reader).

**Backing table:** `signal_queue`
**Module path:** `artemis_os/services/signal_queue.py`

## Interface

```python
from typing import Optional
from artemis_os.schemas.signal import Signal

def write(signal: Signal) -> None:
    """
    Write a new signal to the queue.

    - Validates signal against schema before INSERT
    - Sets status to 'pending_qualification'
    - Raises DuplicateSignalError if signal_id collides
    - Raises ValidationError if schema invalid
    - Idempotent on (district_id, reason_code, embedding_hash) within the
      memory_layer dedupe window (see dedupe rules in schemas/signal.md)
    """

def read_pending(limit: int = 50) -> list[Signal]:
    """
    Read pending_qualification signals for the Qualifier to process.

    - Orders by urgency.tier (hot > standard > enrichment), then discovered_at ASC
    - Atomically transitions status from 'pending_qualification' to 'in_qualification'
    - Uses SELECT FOR UPDATE SKIP LOCKED to prevent double-processing
    """

def update_status(signal_id: str, new_status: str, reason: Optional[str] = None) -> None:
    """
    Update signal status.

    - Enforces valid status transitions (see schemas/signal.md lifecycle)
    - Persists reason to signal.metadata.status_history for auditability
    - Raises InvalidTransitionError on out-of-order transitions
    """

def get(signal_id: str) -> Optional[Signal]:
    """Read a single signal by ID."""

def find_by_district_and_code(
    district_id: str,
    reason_code: str,
    since: Optional[datetime] = None
) -> list[Signal]:
    """
    Used by scouts for dedupe and by Brief Composer for related history.
    """
```

## Status transition matrix (validation logic for update_status)

Valid `from → to` transitions:

| From | Allowed transitions |
|---|---|
| `pending_qualification` | `rejected_hard_filter`, `in_qualification` |
| `in_qualification` | `rejected_low_fit`, `qualified` |
| `qualified` | `brief_composed` |
| `brief_composed` | `pending_human_review` |
| `pending_human_review` | `approved`, `rejected_by_human`, `snoozed` |
| `snoozed` | `pending_human_review` (when snooze expires) |
| `approved` | `in_content_preparation` |
| `in_content_preparation` | `sent_to_writing_studio`, `content_preparation_failed` |

Any other transition raises `InvalidTransitionError`.

## Concurrency notes for Codex

- The Qualifier runs as a single worker process polling every 5 minutes. No competing readers in v1.
- Scout writes are concurrent. Use database-level uniqueness on `signal_id` (PK) and `fingerprint` to prevent duplicates.
- If multiple scouts emit the same fingerprint within the dedupe window, the second write fails — that's correct behavior, log a debug message and move on.

## Implementation guidance

- Use psycopg with connection pooling. Suggested pool size: 5 for scouts, 2 for Qualifier.
- Wrap status updates in transactions. Never use auto-commit.
- Use `RETURNING` clauses on UPDATE to get the post-update row back in one round-trip.

## Failure modes

- DB unreachable → scouts retry with exponential backoff (10s, 30s, 60s) up to 3 times before logging a fatal error and exiting their scheduled run.
- Constraint violation (duplicate fingerprint) → log as INFO, scout continues. This is expected behavior, not an error.
- Schema validation failure → log as ERROR with the raw signal payload, do not crash the scout. The scout's next-run dedupe will pick up the genuine signal if it surfaces again.
