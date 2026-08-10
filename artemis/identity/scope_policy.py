"""M3: Identity-aware scope enforcement (access control).

Single source of truth for the allowed-scope policy.  Called by:
  - artemis/routes/memory.py  (HTTP memory API)
  - artemis/floating_artemis/memory.py  (agent retrieval path)
  - artemis/routes/floating_artemis.py  (D11 floating-assistant identity resolution)

CARDINAL RULE — FAIL CLOSED:
On ANY uncertainty (unresolved identity, unknown agent_id, resolver error),
return NOTHING / deny.  NEVER fall back to "all scopes."  A bug that hides
data is acceptable; a bug that leaks data is a security failure.

Access matrix (Lead-confirmed 2026-06-14):
  Owner (jon.fila@amiralearning.com)  → ALL scopes
  Marketing human (any other authed user) → marketing-shared + own personal:<user_id>
  Agent callie                            → marketing scopes, no personal:*, no agent:artemis
  Agent artemis                           → ALL
  Worker                                  → only the explicit scope handed to it
  Unknown / unresolved                    → DENY (empty list)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from artemis.memory.schemas import Scope

_logger = logging.getLogger(__name__)

# Owner email — single source of truth.  Lowercase.
OWNER_EMAIL = "jon.fila@amiralearning.com"

# Marketing-shared scopes that any authed marketing human (and Callie) may read.
# ScopeKind literals that are marketing-shared without a specific scope_id constraint.
_MARKETING_SCOPE_KINDS: frozenset[str] = frozenset(
    {
        "workspace",  # workspace:marketing (and any other workspace:* currently)
        "campaign_family",  # campaign_family:*
        "district",  # district:*
        "account",  # account:*
        "person",  # person:*
        "global",  # global:*
        "pipeline",  # pipeline:*
        "meeting",  # meeting:*
    }
)

# scope_kind values that are ALWAYS personal — never exposed to marketing.
_PERSONAL_SCOPE_KINDS: frozenset[str] = frozenset({"personal"})

# scope_kind=agent, scope_id values that are personal/owner-only.
_AGENT_PERSONAL_SCOPE_IDS: frozenset[str] = frozenset({"artemis", "floating-artemis"})

# scope_kind=agent, scope_id values that are marketing-shared.
_AGENT_MARKETING_SCOPE_IDS: frozenset[str] = frozenset({"callie"})


@dataclass(frozen=True)
class ScopeAllowance:
    """Describes what a caller may read.

    allow_all: bool
        When True the caller may read every scope (owner / agent:artemis).
    allowed_scope_kinds: frozenset[str]
        Scope-kind values the caller may read (wildcard — any scope_id).
        Used when caller has blanket access to a kind (e.g. marketing humans
        may read all campaign_family:* scopes).
    allowed_agent_ids: frozenset[str]
        For scope_kind="agent", which scope_id values are permitted.
    personal_user_id: int | None
        When set, the caller may also read personal:<personal_user_id>.
        Only their own personal scope — never another's.
    denied: bool
        When True the caller is denied everything (fail-closed default).
    """

    allow_all: bool = False
    allowed_scope_kinds: frozenset[str] = field(default_factory=frozenset)
    allowed_agent_ids: frozenset[str] = field(default_factory=frozenset)
    personal_user_id: int | None = None
    denied: bool = False

    def permits(self, scope_kind: str, scope_id: str) -> bool:
        """Return True iff this allowance permits reading (scope_kind, scope_id).

        Fail-closed: any unexpected path returns False.
        """
        try:
            if self.denied:
                return False
            if self.allow_all:
                return True
            # personal:*
            if scope_kind == "personal":
                if self.personal_user_id is None:
                    return False
                return scope_id == str(self.personal_user_id)
            # agent:*
            if scope_kind == "agent":
                return scope_id in self.allowed_agent_ids
            # blanket scope-kind access
            if scope_kind in self.allowed_scope_kinds:
                return True
            return False
        except Exception:
            _logger.exception(
                "ScopeAllowance.permits raised unexpectedly for %s:%s — denying",
                scope_kind,
                scope_id,
            )
            return False

    def filter_scopes(self, scopes: list[Scope]) -> list[Scope]:
        """Return only the scopes the caller is permitted to read."""
        return [s for s in scopes if self.permits(s.scope_kind, s.scope_id)]


# ── Public resolver ───────────────────────────────────────────────────────────


def allowance_for_owner() -> ScopeAllowance:
    """Owner (Jon) — ALL scopes."""
    return ScopeAllowance(allow_all=True)


def allowance_for_marketing_human(user_id: int) -> ScopeAllowance:
    """Any other authed human — marketing-shared + own personal:<user_id>."""
    return ScopeAllowance(
        allow_all=False,
        allowed_scope_kinds=_MARKETING_SCOPE_KINDS,
        allowed_agent_ids=_AGENT_MARKETING_SCOPE_IDS,
        personal_user_id=user_id,
    )


def allowance_for_agent_callie() -> ScopeAllowance:
    """Callie — marketing scopes + agent:callie, NO personal:*, NO agent:artemis."""
    return ScopeAllowance(
        allow_all=False,
        allowed_scope_kinds=_MARKETING_SCOPE_KINDS,
        allowed_agent_ids=_AGENT_MARKETING_SCOPE_IDS,
        personal_user_id=None,
    )


# Scope kinds Kai may read — enablement scope only.
_ENABLEMENT_SCOPE_KINDS: frozenset[str] = frozenset({"enablement"})


def allowance_for_agent_kai() -> ScopeAllowance:
    """Kai — enablement-scoped read-only. NO personal:*, NO agent:artemis, NO marketing."""
    return ScopeAllowance(
        allow_all=False,
        allowed_scope_kinds=_ENABLEMENT_SCOPE_KINDS,
        allowed_agent_ids=frozenset({"kai"}),
        personal_user_id=None,
    )


def allowance_for_agent_artemis() -> ScopeAllowance:
    """Artemis — ALL scopes (Jon's PA)."""
    return ScopeAllowance(allow_all=True)


def allowance_for_agent_ares() -> ScopeAllowance:
    """Ares — owner-private build partner. Reads his own agent scope plus
    Artemis's context (the shared 'one brain'), and NOTHING else.

    NOT allow_all: Ares must never read marketing, enablement, or other humans'
    personal scopes. Jon is the owner, so his personal context lives in the
    agent:artemis / agent:floating-artemis scopes (the personal:<user_id>
    mechanism is for non-owner humans) — granting those here gives Ares Jon's
    context without opening the marketing/enablement surfaces.

    Coworkers and other agents must NOT be able to read agent:ares; that is
    enforced structurally by their own allowances never listing 'ares'.
    """
    return ScopeAllowance(
        allow_all=False,
        allowed_agent_ids=frozenset({"ares", "artemis", "floating-artemis"}),
        personal_user_id=None,
    )


def allowance_denied() -> ScopeAllowance:
    """Deny all — returned for unknown/unresolved identities."""
    return ScopeAllowance(denied=True)


def allowed_scopes_for_email(email: str, user_id: int) -> ScopeAllowance:
    """Resolve allowance from a verified human email + DB user_id.

    Owner → all.
    Any other authed user → marketing-shared + own personal:<user_id>.
    Empty/None email → deny (fail-closed).
    """
    if not email or not isinstance(email, str):
        _logger.warning("allowed_scopes_for_email: blank/None email — denying")
        return allowance_denied()
    if email.strip().lower() == OWNER_EMAIL:
        return allowance_for_owner()
    if not isinstance(user_id, int) or user_id <= 0:
        _logger.warning("allowed_scopes_for_email: invalid user_id=%r — denying", user_id)
        return allowance_denied()
    return allowance_for_marketing_human(user_id)


def allowed_scopes_for_agent(agent_id: str) -> ScopeAllowance:
    """Resolve allowance from an agent_id string.

    "artemis" / "floating-artemis" → all.
    "callie" → marketing only.
    Unknown → deny (fail-closed).
    """
    if not agent_id or not isinstance(agent_id, str):
        _logger.warning("allowed_scopes_for_agent: blank/None agent_id — denying")
        return allowance_denied()
    normalized = agent_id.strip().lower()
    if normalized in ("artemis", "floating-artemis"):
        return allowance_for_agent_artemis()
    if normalized == "callie":
        return allowance_for_agent_callie()
    if normalized == "kai":
        return allowance_for_agent_kai()
    if normalized == "ares":
        return allowance_for_agent_ares()
    _logger.warning("allowed_scopes_for_agent: unknown agent_id=%r — denying", agent_id)
    return allowance_denied()


def resolve_agent_id_from_email(email: str) -> str:
    """D11: SERVER-SIDE floating-assistant resolution from caller identity.

    Owner → "artemis".
    Any other authed user → "callie".
    Unknown/blank → "callie" (never artemis for non-owners — fail-closed toward
    marketing assistant, not personal).
    """
    if not email or not isinstance(email, str):
        return "callie"
    if email.strip().lower() == OWNER_EMAIL:
        return "artemis"
    return "callie"


# ── SQL filter helpers ────────────────────────────────────────────────────────


def build_scope_sql_clauses(
    allowance: ScopeAllowance,
    *,
    scope_kind_col: str = "scope_kind",
    scope_id_col: str = "scope_id",
) -> list[str]:
    """Return a list of SQL WHERE sub-clauses (OR-joined) enforcing the allowance.

    If allowance.allow_all → returns [] (no additional WHERE needed).
    If allowance.denied    → returns ["1=0"] (always false).

    Callers must OR-join the returned clauses and wrap them in an AND with the
    rest of the query.  The columns are parameterised by name (caller supplies
    SQLAlchemy column references separately).

    NOTE: this helper is for diagnostic/doc purposes.  The actual SQLAlchemy
    enforcement in repository.py uses the Allowance.permits() method on the
    Python side after fetching, but for large tables the routes pass
    scope_kind/scope_id filters directly into the SQL query.
    """
    if allowance.allow_all:
        return []
    if allowance.denied:
        return ["1=0"]

    clauses: list[str] = []

    # personal:<user_id>
    if allowance.personal_user_id is not None:
        clauses.append(
            f"({scope_kind_col} = 'personal' AND {scope_id_col} = '{allowance.personal_user_id}')"
        )

    # agent:<id> for each permitted agent
    for agent_id in sorted(allowance.allowed_agent_ids):
        clauses.append(f"({scope_kind_col} = 'agent' AND {scope_id_col} = '{agent_id}')")

    # blanket scope-kind access
    for sk in sorted(allowance.allowed_scope_kinds):
        clauses.append(f"({scope_kind_col} = '{sk}')")

    return clauses
