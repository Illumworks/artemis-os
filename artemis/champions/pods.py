"""Resolve an author's email domain to a pod, district, state and CSM.

Reads the Cloudflare D1 pod directory over the API. **Deliberately not the
``/pods/lookup`` HTTP route** -- that sits behind Cloudflare Access and a
background job cannot do Google SSO.

The resolution rule is copied from the Worker's own ``lookup()`` rather than
reinvented, because getting it subtly wrong is the failure that matters here.
Two parts of it earn their keep:

* **Consumer mail domains never reach the database at all.** The build script
  drops them, because ``gmail.com`` was recorded in Salesforce against a real
  Arizona district and would otherwise have placed every personal-address
  Champion there. This module additionally refuses them by name, so the
  guarantee does not depend on a build script staying correct.
* **A domain claimed by accounts in different pods resolves to nothing.**
  Naming one arbitrary district is worse than admitting we do not know -- a
  unique match in an incomplete table is confidently wrong. Those rows go to
  the needs-a-decision bucket instead.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

#: The pod directory. Same database the Worker serves /pods from.
PODS_DATABASE_ID = "e173a6c5-29c4-468d-8550-6a2ea91f68b8"

#: Belt and braces: the build script already excludes these. A personal address
#: is a real Champion whose district we do not know, not a district.
CONSUMER_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "ymail.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "comcast.net",
        "att.net",
        "verizon.net",
        "sbcglobal.net",
        "protonmail.com",
        "proton.me",
    }
)

_SECRETS = os.path.expanduser("~/.artemis/secrets.env")


class PodDirectoryError(RuntimeError):
    """The pod directory could not be read. Never silently 'unassigned'."""


@dataclass(frozen=True)
class PodMatch:
    domain: str
    pod_slug: str | None
    pod_name: str | None
    district: str | None
    state: str | None
    csm_email: str | None
    is_ambiguous: bool
    needs_decision: bool


def _cloudflare_credentials() -> tuple[str, str]:
    """Token and account id, from the environment or ~/.artemis/secrets.env.

    secrets.env is where the Cloudflare deploy scripts already source these
    from, so they are read there rather than duplicated into the app's .env --
    one copy, and the runbooks keep working as written.
    """
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    if token and account:
        return token, account

    try:
        with open(_SECRETS, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip().removeprefix("export ").strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                value = value.strip().strip("'\"")
                if key.strip() == "CLOUDFLARE_API_TOKEN" and not token:
                    token = value
                elif key.strip() == "CLOUDFLARE_ACCOUNT_ID" and not account:
                    account = value
    except OSError as exc:
        raise PodDirectoryError(f"Could not read {_SECRETS}: {exc}") from exc

    if not (token and account):
        raise PodDirectoryError(
            "CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID are not set and were not "
            f"found in {_SECRETS}. The pod join cannot run without them."
        )
    return token, account


async def load_domain_directory(
    client: httpx.AsyncClient, *, database_id: str = PODS_DATABASE_ID
) -> dict[str, PodMatch]:
    """The whole domain -> pod map, in one query.

    9,068 domains is small enough to pull wholesale, and one query beats a
    lookup per author. Reads ``domain_lookup``, the view that layers hand-made
    overrides over the Salesforce-derived table -- the same view the Worker
    reads, so an admin correction takes effect here with no redeploy.
    """
    token, account = _cloudflare_credentials()
    sql = (
        "SELECT l.domain, l.pod_slug, l.account_name, l.state, l.csm_email, "
        "l.is_ambiguous, p.display_name AS pod_name "
        "FROM domain_lookup l LEFT JOIN pod p ON p.slug = l.pod_slug"
    )
    resp = await client.post(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/d1/database/{database_id}/query",
        headers={"Authorization": f"Bearer {token}"},
        json={"sql": sql},
    )
    if resp.status_code != 200:
        raise PodDirectoryError(f"D1 query failed: {resp.status_code} {resp.text[:200]}")
    payload = resp.json()
    if not payload.get("success"):
        raise PodDirectoryError(f"D1 query failed: {payload.get('errors')}")

    rows = (payload.get("result") or [{}])[0].get("results", [])
    directory: dict[str, PodMatch] = {}
    for row in rows:
        domain = str(row.get("domain") or "").strip().lower()
        if not domain:
            continue
        directory[domain] = _to_match(domain, row)
    logger.info("champions: loaded %d domains from the pod directory", len(directory))
    return directory


def _to_match(domain: str, row: dict[str, object]) -> PodMatch:
    slug = row.get("pod_slug")
    slug_str = str(slug) if slug else None
    ambiguous = bool(row.get("is_ambiguous"))
    resolved = bool(slug_str) and slug_str != "unassigned"

    # The Worker's rule: a caller that finds the answer unsettled routes the
    # item to the unassigned bucket and surfaces the choice. It does not pick.
    settled = resolved and not ambiguous
    return PodMatch(
        domain=domain,
        pod_slug=slug_str if settled else None,
        pod_name=str(row["pod_name"]) if settled and row.get("pod_name") else None,
        district=str(row["account_name"]) if settled and row.get("account_name") else None,
        state=str(row["state"]) if settled and row.get("state") else None,
        csm_email=str(row["csm_email"]) if settled and row.get("csm_email") else None,
        is_ambiguous=ambiguous,
        needs_decision=not settled,
    )


def resolve(domain: str | None, directory: dict[str, PodMatch]) -> PodMatch | None:
    """One domain against a loaded directory. ``None`` means do not place it.

    A consumer domain returns None rather than an unassigned match: it is not a
    district that needs a decision, it is a person using a personal address, and
    putting it in the decision queue would bury the domains a human can fix.
    """
    if not domain:
        return None
    key = domain.strip().lower()
    if key in CONSUMER_DOMAINS:
        return None
    return directory.get(key)
