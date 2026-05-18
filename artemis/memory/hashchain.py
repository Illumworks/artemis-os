"""SHA-256 hash chain for raw_inputs.

Canonical serialization recipe (documented here; must never change silently):
  - Fields included (sorted key order in the JSON object):
      actor, created_at_iso, payload, prev_hash, scope_id, scope_kind,
      source_id, source_kind
  - Serialization: json.dumps with sort_keys=True, separators=(",", ":"),
    ensure_ascii=False. Datetimes → ISO 8601 with UTC offset. None → null.
  - Hash: SHA-256 of the UTF-8 encoding of the canonical JSON string.

This recipe is frozen. Any change requires a new migration and a
chain-break marker row explaining the format boundary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def canonical_form(
    *,
    source_kind: str,
    source_id: str | None,
    actor: str | None,
    scope_kind: str,
    scope_id: str,
    payload: dict[str, Any],
    created_at: datetime,
    prev_hash: str | None,
) -> str:
    """Deterministic JSON serialization for hashing.

    Keys are sorted alphabetically. None becomes JSON null.
    datetime is serialized to ISO 8601 with UTC offset.
    """
    doc: dict[str, Any] = {
        "actor": actor,
        "created_at_iso": created_at.isoformat(),
        "payload": payload,
        "prev_hash": prev_hash,
        "scope_id": scope_id,
        "scope_kind": scope_kind,
        "source_id": source_id,
        "source_kind": source_kind,
    }
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_this_hash(canonical: str) -> str:
    """SHA-256 hex digest of a canonical form string."""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def payload_sha256(payload: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON of payload alone.

    Stored in payload_hash so rehydration from archive can verify
    the payload was reconstructed correctly without re-deriving this_hash.
    """
    serialized = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass
class ChainResult:
    ok: bool
    row_count: int
    first_break_id: int | None = None
    message: str = field(default="")


async def verify_chain(
    session: AsyncSession,
    scope_kind: str | None = None,
    scope_id: str | None = None,
) -> ChainResult:
    """Walk raw_inputs rows and verify hash chain integrity.

    Without scope filter: full global chain verification — checks both
    prev_hash linkage (each row's prev_hash matches the previous row's
    this_hash) and hash correctness (recomputes this_hash for unarchived rows).

    With scope filter: checks hash correctness only for those rows.
    Linkage verification is skipped for scoped walks because prev_hash in
    scoped rows points to the previous global row (which may be a different
    scope), so linkage across the filtered set would always appear broken.

    Returns ChainResult with first_break_id set if any inconsistency is found.
    """
    from artemis.memory.raw_inputs import RawInput

    stmt = select(RawInput).order_by(RawInput.id)
    if scope_kind is not None:
        stmt = stmt.where(RawInput.scope_kind == scope_kind)
    if scope_id is not None:
        stmt = stmt.where(RawInput.scope_id == scope_id)

    result = await session.execute(stmt)
    rows = list(result.scalars())

    if not rows:
        return ChainResult(ok=True, row_count=0, message="empty chain")

    global_walk = scope_kind is None and scope_id is None
    prev_this_hash: str | None = None

    for row in rows:
        if global_walk and row.prev_hash != prev_this_hash:
            return ChainResult(
                ok=False,
                row_count=len(rows),
                first_break_id=row.id,
                message=(
                    f"chain break at id={row.id}: "
                    f"prev_hash={row.prev_hash!r} != expected={prev_this_hash!r}"
                ),
            )

        # For unarchived rows with payload intact, recompute and verify this_hash.
        if row.archived_at is None and row.payload is not None:
            canon = canonical_form(
                source_kind=row.source_kind,
                source_id=row.source_id,
                actor=row.actor,
                scope_kind=row.scope_kind,
                scope_id=row.scope_id,
                payload=row.payload,
                created_at=row.created_at,
                prev_hash=row.prev_hash,
            )
            expected = compute_this_hash(canon)
            if row.this_hash != expected:
                return ChainResult(
                    ok=False,
                    row_count=len(rows),
                    first_break_id=row.id,
                    message=(
                        f"hash mismatch at id={row.id}: "
                        f"stored={row.this_hash[:16]}... expected={expected[:16]}..."
                    ),
                )

        prev_this_hash = row.this_hash

    return ChainResult(ok=True, row_count=len(rows), message="chain intact")
