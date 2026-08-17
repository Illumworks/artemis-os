"""SFDC-1 -- read-only Salesforce introspection.

Run this ONCE Jon's Salesforce credentials are installed (Settings ->
Integrations -> Salesforce card, or
POST /api/integrations/providers/salesforce/config) to find out, from the
real org, what SFDC-1 had to assume without a live connection:

  1. Does Account actually have a field meaning "is a customer"? We assumed
     ``Is_Customer__c`` (artemis.config.settings.salesforce_customer_field).
     This prints every Account field whose name or label plausibly means
     that, plus the standard ``Type`` picklist's configured values -- the
     single most common real-world carrier for customer status on a
     standard Salesforce field.
  2. What Opportunity stage names does this org actually use, with each
     stage's IsClosed/IsWon flags -- so the open_opportunity check (which
     relies on the standard, always-present ``IsClosed`` field, not on a
     guessed stage name) can be sanity-checked against real data.

Authenticates via the exact same Client Credentials flow and read-only
client the suppression guard uses (artemis.integrations.salesforce.client).
NO WRITES: a describe() call and one aggregate SOQL query are the only two
Salesforce calls made, both GET. Prints and exits -- nothing here can send
an email, touch a record, or change anything in Salesforce or our own DB.

Usage (after credentials are installed, from the repo root):

    uv run python -m scripts.salesforce_introspect

Exit code 0 on success, 1 if credentials are missing or Salesforce rejects
the connection (the printed message says which).
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from artemis.db import SessionLocal
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

_CUSTOMER_KEYWORDS = ("customer", "client")


def _looks_like_customer_field(field: dict[str, Any]) -> bool:
    name = str(field.get("name") or "").lower()
    label = str(field.get("label") or "").lower()
    return any(kw in name or kw in label for kw in _CUSTOMER_KEYWORDS)


async def _run() -> int:
    async with SessionLocal() as session:
        try:
            cfg = await resolve_salesforce_config(session)
        except MissingProviderConfigError as exc:
            print(f"Salesforce is not configured yet: {exc}")
            print(
                "Install credentials first -- Settings -> Integrations -> Salesforce, "
                "or POST /api/integrations/providers/salesforce/config."
            )
            return 1

        try:
            token = await fetch_access_token(
                login_url=cfg.login_url,
                client_id=cfg.client_id,
                client_secret=cfg.client_secret,
            )
        except SalesforceAuthError as exc:
            print(f"Authentication failed: {exc}")
            return 1

        client = SalesforceClient(token.instance_url, token.access_token)
        print(f"Authenticated. instance_url={token.instance_url}\n")

        # ── 1. Account: candidates for "is a customer" ──────────────────────
        try:
            account_described = await client.describe_sobject("Account")
        except SalesforceAPIError as exc:
            print(f"describe(Account) failed: {exc}")
            return 1

        fields = [f for f in account_described.get("fields", []) if isinstance(f, dict)]
        print(f"Account has {len(fields)} fields total.\n")

        candidates = [f for f in fields if _looks_like_customer_field(f)]
        print("Fields whose name/label plausibly means 'is a customer':")
        if candidates:
            for f in candidates:
                print(f"  - {f.get('name')} (label={f.get('label')!r}, type={f.get('type')})")
        else:
            print("  (none found by name/label match)")

        type_field = next((f for f in fields if f.get("name") == "Type"), None)
        print(
            "\nStandard 'Type' picklist (the most common real-world carrier for "
            "customer status on a standard field):"
        )
        if type_field is not None:
            values = type_field.get("picklistValues") or []
            if values:
                for v in values:
                    print(f"  - {v.get('value')!r} (active={v.get('active')})")
            else:
                print("  (Type is present but has no configured picklist values)")
        else:
            print("  (Account.Type is not present on this org -- unexpected for a standard field)")

        # ── 2. Opportunity: stage names actually in use ─────────────────────
        print("\nOpportunity stage names in use (from real records):")
        try:
            stage_rows = await client.query(
                "SELECT StageName, IsClosed, IsWon, COUNT(Id) cnt FROM Opportunity "
                "GROUP BY StageName, IsClosed, IsWon ORDER BY StageName"
            )
        except SalesforceAPIError as exc:
            print(f"  query failed: {exc}")
            return 1

        if stage_rows:
            for row in stage_rows:
                print(
                    f"  - {row.get('StageName')!r}: {row.get('cnt')} record(s) "
                    f"(IsClosed={row.get('IsClosed')}, IsWon={row.get('IsWon')})"
                )
        else:
            print("  (no Opportunity records found)")

        print(
            "\nNext step: if a real customer-status field is listed above, set "
            "ARTEMIS_SALESFORCE_CUSTOMER_FIELD (and ARTEMIS_SALESFORCE_CUSTOMER_TRUTHY_VALUES "
            "if it is a picklist/text field rather than a plain boolean) to match. If nothing "
            "plausible is listed, Salesforce genuinely has no such field yet -- ask Neil to "
            "confirm before assuming one exists under a different name."
        )
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
