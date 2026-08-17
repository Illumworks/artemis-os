"""SFDC-1 -- Salesforce-backed send suppression.

Jon, verbatim: "is this person already in a conversation, already a
customer, or already emailed?" This module answers exactly that, per
recipient, and is wired into
``artemis.marketing.sends.enqueue_send_for_deliverable`` at the existing
queue-or-skip seam (today's only ``skip_reason`` is ``no_contacts_on_file``).

FAIL CLOSED is the single most important behaviour here, per the brief. Any
failure to reach Salesforce, authenticate, or find the field we ASSUMED
exists on Account maps to ``skip_reason='salesforce_unavailable'`` -- never
to "not a customer". An unsent email costs a day; a wrongly-sent one costs a
relationship with someone sales is mid-negotiation with. Every exception
path in ``check_suppression`` below resolves to suppression, not to a
default "clear to send".

Architecture note -- flagged, not buried. There is no verified, stable join
key between our ``districts`` table and Salesforce ``Account`` (no
``districts.salesforce_account_id`` column), and matching by district name
is exactly the kind of guess the brief warns against: a same-named different
account, or a real customer account filed under a different legal name, are
both realistic misses in either direction. So this module does NOT look up
"the district's Account" at all. Every check is instead keyed off the
RECIPIENT'S OWN EMAIL ADDRESS, matched directly to a Salesforce Contact --
email is a far more reliable natural key than a fuzzy name match:

  - No Salesforce Contact with that email -> nothing is known about this
    person in Salesforce -> not suppressed. This is both the "clean
    prospect" case AND what happens for a district that is not a Salesforce
    customer at all -- there is nothing to find either way.
  - A Contact IS found -> its AccountId drives the existing_customer and
    open_opportunity checks, and the Contact's own Id drives the
    recent_sales_contact (Task) check.

Known gap this leaves, said plainly rather than silently built around: a
BRAND NEW contact at a district whose Salesforce Account IS an existing
customer, who has never personally been entered as a Salesforce Contact,
will NOT be suppressed by this module -- there is nothing to look them up
by. Closing that gap needs a real district<->Account mapping (a migration
plus a matching/sync job), which the brief did not specify and which this
slice does not guess at. Worth a follow-up brief once Jon/Neil can say what
a reliable join key would even be.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.integrations.config_resolver import (
    MissingProviderConfigError,
    resolve_salesforce_config,
)
from artemis.integrations.salesforce.client import (
    SalesforceAPIError,
    SalesforceAuthError,
    SalesforceClient,
    fetch_access_token,
)
from artemis.marketing.contacts import upsert_salesforce_contact

logger = logging.getLogger(__name__)

SKIP_EXISTING_CUSTOMER = "existing_customer"
SKIP_OPEN_OPPORTUNITY = "open_opportunity"
SKIP_RECENT_SALES_CONTACT = "recent_sales_contact"
SKIP_SALESFORCE_UNAVAILABLE = "salesforce_unavailable"

# Priority when multiple recipients in one batch trip different reasons (see
# check_suppression_for_recipients). salesforce_unavailable always wins --
# once one lookup failed, no other result in the same batch can be trusted.
_REASON_PRIORITY = {
    SKIP_SALESFORCE_UNAVAILABLE: 0,
    SKIP_EXISTING_CUSTOMER: 1,
    SKIP_OPEN_OPPORTUNITY: 2,
    SKIP_RECENT_SALES_CONTACT: 3,
}


@dataclass(frozen=True)
class SuppressionResult:
    suppressed: bool
    skip_reason: str | None
    detail: str


def _soql_escape(value: str) -> str:
    """Escape a value for embedding in a SOQL string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _is_customer_value(value: Any) -> bool:
    truthy_values = [
        v.strip().lower()
        for v in settings.salesforce_customer_truthy_values.split(",")
        if v.strip()
    ]
    if truthy_values:
        return str(value).strip().lower() in truthy_values
    # No configured value list: treat the field as a plain boolean checkbox.
    # Deliberately strict (`is True`, not `bool(value)`) -- a truthy non-bool
    # (e.g. a non-empty picklist string that happens to not be "false") must
    # never be misread as "is a customer".
    return value is True


async def _get_client(session: AsyncSession) -> SalesforceClient:
    """Resolve config and mint an authenticated client.

    Raises SalesforceAuthError/MissingProviderConfigError on any failure --
    callers must treat every exception here as salesforce_unavailable.
    """
    cfg = await resolve_salesforce_config(session)
    token = await fetch_access_token(
        login_url=cfg.login_url, client_id=cfg.client_id, client_secret=cfg.client_secret
    )
    return SalesforceClient(token.instance_url, token.access_token)


async def _account_customer_field_present(client: SalesforceClient) -> bool:
    """Describe Account and check the configured customer field actually exists.

    This is the fail-closed guard for OUR OWN assumption about the field
    name (docs/marketing-intelligence-direction.md's open question on
    `is customer`; see scripts/salesforce_introspect.py for how to find the
    real one). A connectivity/API failure propagates as an exception here,
    same as everywhere else in this module; this function's specific job is
    the case where Salesforce answered FINE but the field we guessed simply
    is not on Account -- which, without this check, would silently read as
    None -> "not a customer" for every single account, the exact failure
    direction the brief calls the worst possible outcome.
    """
    described = await client.describe_sobject("Account")
    field_names = {f.get("name") for f in described.get("fields", []) if isinstance(f, dict)}
    return settings.salesforce_customer_field in field_names


async def check_suppression(
    session: AsyncSession,
    *,
    district_id: int,
    email: str,
    enrich: bool = True,
) -> SuppressionResult:
    """Run the Salesforce suppression check for ONE recipient email.

    Never raises -- every failure mode (missing credentials, auth failure,
    network error, our assumed field missing from describe, any other
    unexpected exception) is caught here and turned into
    ``SuppressionResult(suppressed=True, skip_reason='salesforce_unavailable')``.
    This IS the fail-closed contract the brief is about; do not add a bare
    ``except`` that returns "not suppressed" anywhere in this function.

    ``enrich`` controls whether a Contact match also writes back into
    ``district_contacts`` (artemis.marketing.contacts.upsert_salesforce_contact).
    The send path wants this (item 2 of the brief: better data than Argus's
    prose extraction); Callie's read-only tool
    (artemis.floating_artemis.tools.salesforce_tools) passes enrich=False so
    a layer-1 "just tell me" tool never has a DB write side effect.
    """
    try:
        client = await _get_client(session)

        if not await _account_customer_field_present(client):
            return SuppressionResult(
                True,
                SKIP_SALESFORCE_UNAVAILABLE,
                f"configured customer field {settings.salesforce_customer_field!r} is not "
                "present on Salesforce Account -- our assumed field name is wrong; treating "
                "as unavailable rather than silently answering 'not a customer'",
            )

        contact_records = await client.query(
            "SELECT Id, AccountId, Name, Title, Email, Phone FROM Contact WHERE Email = "
            f"'{_soql_escape(email)}' LIMIT 1"
        )
        if not contact_records:
            return SuppressionResult(False, None, "no Salesforce Contact found for this email")

        contact = contact_records[0]
        contact_id = str(contact.get("Id") or "")
        account_id = contact.get("AccountId")

        if enrich:
            try:
                await upsert_salesforce_contact(
                    session,
                    district_id=district_id,
                    email=email,
                    name=str(contact.get("Name") or "").strip() or email,
                    title=(contact.get("Title") or None),
                    phone=(contact.get("Phone") or None),
                    external_id=contact_id or None,
                )
            except Exception:
                # Enrichment is best-effort and must never block or flip the
                # suppression decision -- see the module's fail-closed
                # contract, which is about SALESFORCE reachability, not
                # about our own write to our own table.
                logger.warning(
                    "check_suppression: contact enrichment failed for email=%s district_id=%s",
                    email,
                    district_id,
                    exc_info=True,
                )

        if account_id:
            account_records = await client.query(
                f"SELECT Id, {settings.salesforce_customer_field} FROM Account WHERE Id = "
                f"'{_soql_escape(str(account_id))}' LIMIT 1"
            )
            if account_records and _is_customer_value(
                account_records[0].get(settings.salesforce_customer_field)
            ):
                return SuppressionResult(
                    True,
                    SKIP_EXISTING_CUSTOMER,
                    f"Salesforce Account {account_id} is flagged as an existing customer "
                    f"({settings.salesforce_customer_field})",
                )

            opp_records = await client.query(
                "SELECT Id FROM Opportunity WHERE AccountId = "
                f"'{_soql_escape(str(account_id))}' AND IsClosed = false LIMIT 1"
            )
            if opp_records:
                return SuppressionResult(
                    True,
                    SKIP_OPEN_OPPORTUNITY,
                    f"Salesforce Account {account_id} has an open Opportunity "
                    f"(id={opp_records[0].get('Id')})",
                )

        if contact_id:
            window_days = settings.salesforce_recent_contact_window_days
            window_start = datetime.now(UTC) - timedelta(days=window_days)
            task_records = await client.query(
                "SELECT Id, CreatedDate FROM Task WHERE WhoId = "
                f"'{_soql_escape(contact_id)}' AND TaskSubtype = 'Email' AND CreatedDate >= "
                f"{window_start.strftime('%Y-%m-%dT%H:%M:%SZ')} ORDER BY CreatedDate DESC LIMIT 1"
            )
            if task_records:
                return SuppressionResult(
                    True,
                    SKIP_RECENT_SALES_CONTACT,
                    f"Salesforce Contact {contact_id} was emailed on "
                    f"{task_records[0].get('CreatedDate')}, inside the {window_days}-day window",
                )

        return SuppressionResult(
            False, None, "known Salesforce contact; no suppression rule tripped"
        )

    except (SalesforceAuthError, SalesforceAPIError, MissingProviderConfigError) as exc:
        logger.warning(
            "check_suppression: Salesforce unavailable for email=%s district_id=%s: %s",
            email,
            district_id,
            exc,
        )
        return SuppressionResult(True, SKIP_SALESFORCE_UNAVAILABLE, str(exc))
    except Exception as exc:  # noqa: BLE001 -- fail-closed contract: ANY failure suppresses
        logger.error(
            "check_suppression: unexpected error for email=%s district_id=%s -- failing closed",
            email,
            district_id,
            exc_info=True,
        )
        return SuppressionResult(True, SKIP_SALESFORCE_UNAVAILABLE, f"unexpected error: {exc}")


async def check_suppression_for_recipients(
    session: AsyncSession,
    recipients: list[dict[str, Any]],
) -> SuppressionResult:
    """Run check_suppression for every recipient in an enqueue-time snapshot.

    All-or-nothing at the deliverable level, matching campaign_sends' existing
    one-row/one-skip_reason shape (see artemis.marketing.sends) -- this
    deliberately does NOT partially send to some recipients and skip others
    within the same CampaignSend row. Recipient shape matches the snapshot
    produced by resolve_recipients_for_candidate: {"contact_id", "district_id",
    "name", "email", "title"}.

    Stops early the moment ANY recipient comes back salesforce_unavailable --
    no result from the rest of the batch can be trusted once one lookup
    failed, and there's no reason to keep spending API calls once the whole
    batch is already going to be skipped for that reason.
    """
    if not recipients:
        return SuppressionResult(False, None, "no recipients to check")

    best: SuppressionResult | None = None
    best_priority = len(_REASON_PRIORITY)
    for recipient in recipients:
        email = str(recipient.get("email") or "")
        district_id = recipient.get("district_id")
        if not email or district_id is None:
            continue
        result = await check_suppression(session, district_id=int(district_id), email=email)
        if result.suppressed and result.skip_reason is not None:
            priority = _REASON_PRIORITY[result.skip_reason]
            if priority < best_priority:
                best = result
                best_priority = priority
        if best is not None and best.skip_reason == SKIP_SALESFORCE_UNAVAILABLE:
            break

    if best is not None:
        return best
    return SuppressionResult(False, None, "no recipient tripped a suppression rule")
