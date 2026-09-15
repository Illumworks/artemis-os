"""Whether an address could actually receive mail.

Three read sites decide whether a district has "someone we can write to":
``contact_db_stub.has_contact``, ``resolve_recipients_for_candidate`` and the
signal-queue ``routable`` classification. All three asked only whether ``email``
was non-NULL.

**That is not the same question, and the gap is not hypothetical.** Every stored
address today is on a reserved domain — ``alex.johnson@fort-worth-isd.example``,
``morgan.smith@ci4-e2e-fort-bend-isd-v2.example`` — left behind by tests. So
``has_contact`` answered **true** for Fort Worth ISD, the queue would have called
its signals routable, and a send would have been queued to an address that by
specification can never exist.

``.example``, ``.test``, ``.invalid`` and ``.localhost`` are reserved by RFC 2606
precisely so they can be used in documentation and testing without ever reaching
a real mailbox, as are ``example.com`` / ``.org`` / ``.net``. An address on one is
not a weak contact; it is a guaranteed non-delivery.

This is the same failure the `contacts.py` docstring already describes one step
short of: a read that means "someone real we can send this to" must say so, or it
builds a recipient list that can never go anywhere.

Deliberately narrow. This rejects only what cannot resolve by definition — it is
not a validity check, a syntax check, or a bounce predictor, and it must not grow
into one. A real address at a misspelled domain is a problem for the send
transport's bounce reporting, not for this.
"""

from __future__ import annotations

from typing import Any

#: Reserved by RFC 2606 / RFC 6761. Nothing here can ever receive mail.
_RESERVED_SUFFIXES: tuple[str, ...] = (
    ".example",
    ".test",
    ".invalid",
    ".localhost",
)

#: Reserved second-level domains from the same RFC.
_RESERVED_DOMAINS: frozenset[str] = frozenset({"example.com", "example.org", "example.net"})


def is_deliverable(email: str | None) -> bool:
    """Whether ``email`` could reach a real mailbox.

    False for an absent address and for anything on a reserved domain. Note this
    says nothing about whether the mailbox exists — only that it is not
    disqualified by specification.
    """
    address = (email or "").strip().lower()
    if "@" not in address:
        return False
    local, _, domain = address.rpartition("@")
    # An empty local part ("@district.org") is not an address. Caught by a test
    # rather than by inspection, which is the point of having one.
    if not local.strip() or not domain.strip() or "." not in domain:
        return False
    domain = domain.strip()
    if domain in _RESERVED_DOMAINS:
        return False
    return not domain.endswith(_RESERVED_SUFFIXES)


def deliverable_email_clause(column: Any) -> Any:
    """SQLAlchemy filter for the same rule, for queries that cannot load rows.

    Kept beside :func:`is_deliverable` so the two cannot drift; a query that
    disagreed with the predicate would be the exact bug this module exists for.
    """
    from sqlalchemy import and_, func, not_, or_

    lowered = func.lower(func.trim(column))
    clauses = [column.isnot(None), lowered.like("%@%")]
    clauses += [not_(lowered.like(f"%{suffix}")) for suffix in _RESERVED_SUFFIXES]
    clauses.append(
        not_(or_(*[lowered.like(f"%@{domain}") for domain in sorted(_RESERVED_DOMAINS)]))
    )
    return and_(*clauses)
