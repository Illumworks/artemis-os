"""Read-only Salesforce REST API client (SFDC-1).

Structurally read-only, on purpose: ``SalesforceClient`` exposes exactly two
public methods, ``describe_sobject`` and ``query``, and both issue GET
requests only. There is no create/update/delete/patch method anywhere in
this class, and none should ever be added -- SFDC-1's entire reason to exist
is a suppression GUARD in front of the send pipeline
(artemis.marketing.salesforce_suppression); a client that could write to
Salesforce would defeat the one guarantee everything downstream relies on.
``test_sfdc1_salesforce_client.py`` asserts the class's public method set is
exactly ``{"describe_sobject", "query"}`` so a future edit that adds a write
method fails a test immediately, not in front of a real customer.

The only POST this module ever issues is the OAuth 2.0 Client Credentials
token exchange (``fetch_access_token``). That authenticates against
Salesforce's identity/token service, not against any Salesforce CRM object,
and mutates no Account/Contact/Opportunity/Task data -- it is the standard,
documented server-to-server auth handshake, not a "write" in the sense this
module's read-only guarantee is about.

Auth flow: OAuth 2.0 Client Credentials (server-to-server, no user redirect,
confirmed with Neil -- see briefs/sfdc-1-read-and-suppress.md):

    POST {login_url}/services/oauth2/token
      grant_type=client_credentials&client_id=...&client_secret=...
    -> {"access_token": ..., "instance_url": ..., "token_type": "Bearer", ...}

No refresh_token is issued under this grant (Salesforce's own docs are
explicit that Client Credentials does not support refresh tokens), so a
fresh access_token is fetched per suppression check here rather than cached
across a long-running process -- the token exchange is cheap and this client
is only ever called from the send path, never a hot loop.

Read (SOQL query / describe) calls go through
``/services/data/{API_VERSION}/...`` using whatever ``instance_url`` the
token exchange returned -- Salesforce may return a different instance per
org/session, so ``instance_url`` must always come from the token response,
never be assumed or cached long-term.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Pinned to a long-stable, well-documented REST API version. Salesforce keeps
# old API versions working indefinitely (backward compatible), so this is not
# a "dependency" in the semver sense the org's 7-day freshness rule is about --
# it is an HTTP wire-format version, and an old, thoroughly stable one is the
# SAFER choice here, not a risk to mitigate.
_API_VERSION = "v59.0"


class SalesforceAPIError(Exception):
    """A Salesforce REST call (describe or query) returned a non-2xx response."""

    def __init__(self, operation: str, status: int, detail: str = "") -> None:
        super().__init__(f"Salesforce {operation} failed ({status}): {detail[:200]}")
        self.operation = operation
        self.status = status
        self.detail = detail


class SalesforceAuthError(Exception):
    """The Client Credentials token exchange itself failed.

    Callers (artemis.marketing.salesforce_suppression) must treat this
    identically to any other "Salesforce unavailable" failure: fail closed.
    """


@dataclass(frozen=True)
class SalesforceToken:
    access_token: str
    instance_url: str


async def fetch_access_token(
    *, login_url: str, client_id: str, client_secret: str
) -> SalesforceToken:
    """Exchange client_id/client_secret for a bearer token (Client Credentials grant).

    Raises SalesforceAuthError on ANY failure: network error, non-2xx
    response, or a 2xx response missing the fields we need. Never returns a
    partially-populated token.
    """
    url = f"{login_url.rstrip('/')}/services/oauth2/token"
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
    except httpx.HTTPError as exc:
        raise SalesforceAuthError(f"token exchange network error: {exc}") from exc

    if not resp.is_success:
        raise SalesforceAuthError(
            f"token exchange rejected ({resp.status_code}): {resp.text[:200]}"
        )

    try:
        body: dict[str, Any] = resp.json()
    except Exception as exc:
        raise SalesforceAuthError(f"token exchange returned non-JSON response: {exc}") from exc

    access_token = str(body.get("access_token") or "")
    instance_url = str(body.get("instance_url") or "")
    if not access_token or not instance_url:
        raise SalesforceAuthError(
            "token exchange response missing access_token/instance_url"
        )
    return SalesforceToken(access_token=access_token, instance_url=instance_url)


class SalesforceClient:
    """Read-only Salesforce REST API surface. See module docstring.

    Deliberately has NO constructor dependency on stored credentials -- it
    takes an already-fetched (instance_url, access_token) pair, so this class
    itself never touches config storage, encryption, or the OAuth token
    endpoint. Composition happens in artemis.marketing.salesforce_suppression.
    """

    def __init__(self, instance_url: str, access_token: str) -> None:
        self._base = instance_url.rstrip("/")
        self._access_token = access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }

    async def describe_sobject(self, sobject: str) -> dict[str, Any]:
        """GET the describe metadata for one sobject (e.g. 'Account').

        Returns the full describe payload -- callers read
        ``result["fields"]`` (each a dict with at least ``name``, ``label``,
        ``type``, and ``picklistValues`` when applicable).
        """
        url = f"{self._base}/services/data/{_API_VERSION}/sobjects/{sobject}/describe/"
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(url, headers=self._headers())
        if not resp.is_success:
            raise SalesforceAPIError(f"describe:{sobject}", resp.status_code, resp.text[:200])
        data: dict[str, Any] = resp.json()
        return data

    async def query(self, soql: str) -> list[dict[str, Any]]:
        """GET /query -- run one SOQL statement, return the 'records' list.

        Only the first page is returned (no ``nextRecordsUrl`` following) --
        every caller in this codebase targets one bounded lookup (a single
        Account/Contact/Opportunity/Task, or an aggregate GROUP BY for the
        introspection script), never a bulk export. Write a LIMIT clause.
        """
        url = f"{self._base}/services/data/{_API_VERSION}/query/"
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(url, headers=self._headers(), params={"q": soql})
        if not resp.is_success:
            raise SalesforceAPIError("query", resp.status_code, resp.text[:200])
        data: dict[str, Any] = resp.json()
        records = data.get("records")
        return list(records) if isinstance(records, list) else []
