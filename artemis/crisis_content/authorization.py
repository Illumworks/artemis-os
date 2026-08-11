"""Per-route approver allowlists for crisis-content decisions (slice B2c, CCA5).

Per ``docs/crisis-content-approval-pipeline.md`` "Routing" and
``briefs/cca5-approval-loop.md`` "Authorization": the two routes have
DIFFERENT, non-overlapping approvers, and getting the direction backwards
would still look like it works (both are "does this email match a list").

    route     who may decide
    ------    -----------------------------------------------
    asset     Jon only
    copy      Angela, Hannah, Jaclyn -- any ONE is sufficient; Jon is
              deliberately rejected here (he does not approve copy)

This deliberately widens the existing Jon-and-Missy-only rule for Kai's
side-effecting tools (``artemis.enablement.actions.is_authorized_for_kai_actions``)
-- but ONLY for this pipeline. This module does not import, call, or modify
that helper, or any other existing authorization helper in the repo. It is a
new, narrowly-scoped allowlist for crisis-content decisions alone.

The lists live in settings (``crisis_content_asset_approver_emails`` /
``crisis_content_copy_approver_emails``), not as inline literals, so adding
or removing an approver is a config change, not a code edit.

Authorization here is checked against an EMAIL, resolved server-side from
the verified Slack payload's ``user.id`` (see
``artemis.crisis_content.slack_actions``) -- never from anything a click's
``value`` or a modal's ``private_metadata`` carries.
"""

from __future__ import annotations

from artemis.config import get_settings
from artemis.crisis_content.transitions import Route

__all__ = [
    "asset_route_approver_emails",
    "copy_route_approver_emails",
    "approver_emails_for_route",
    "is_authorized_for_route",
]


def _parse_emails(raw: str) -> frozenset[str]:
    """Split a comma-separated email-list setting into a lowercased set."""
    return frozenset(part.strip().lower() for part in (raw or "").split(",") if part.strip())


def asset_route_approver_emails() -> frozenset[str]:
    """Emails permitted to decide the 'asset' route. Jon only, by design."""
    return _parse_emails(get_settings().crisis_content_asset_approver_emails)


def copy_route_approver_emails() -> frozenset[str]:
    """Emails permitted to decide the 'copy' route -- any one of Angela/Hannah/Jaclyn."""
    return _parse_emails(get_settings().crisis_content_copy_approver_emails)


def approver_emails_for_route(route: Route) -> frozenset[str]:
    """The allowlist for one route. ``route`` is exactly ``'asset'`` or ``'copy'``."""
    if route == "asset":
        return asset_route_approver_emails()
    return copy_route_approver_emails()


def is_authorized_for_route(email: str | None, route: Route) -> bool:
    """Fail-closed: a blank/``None`` email (unresolved Slack user) denies.

    Case-insensitive on the email, to tolerate whatever casing the directory
    sync or Slack profile happens to carry.
    """
    if not email:
        return False
    return email.strip().lower() in approver_emails_for_route(route)
