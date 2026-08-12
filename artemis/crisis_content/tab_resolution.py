"""Resolve which Google Docs tab each polled review card lives on (CCA13).

Two things were blocked on the same missing fact -- which tab a card lives
on -- and both are unlocked by this module:

1. **The test lane.** Jon duplicated a card into a tab titled
   ``Content To Review (TESTING)`` so he can exercise approve / edit /
   attach-image / reopen without the channel or the external vendor (Jen,
   DigiGeeks) seeing any of it. A card is a test card iff its tab title
   contains ``settings.crisis_content_test_tab_marker`` (default
   ``"TESTING"``, case-insensitive) -- see ``CardTabInfo.is_test`` and
   ``_is_test_title`` below.
2. **The ``?tab=`` deep link.** CCA12 shipped ``Transition.tab_id`` typed,
   tested, and deliberately unpopulated (see that field's docstring) because
   populating it needs a second, live Docs JSON fetch this package's
   read path (the HTML export -- see ``export_client.py``) never makes.
   This module is that fetch.

**One ``documents.get`` call per poll tick, never per card.** The HTML
export the poller already fetches is tab-agnostic (see ``parser.py``'s
``_is_review_card_table`` docstring) -- it cannot tell us which tab a card
came from. This module makes exactly ONE additional network call
(``_fetch_document``) per invocation, then resolves every card in
``cards`` against that single, already-fetched document in memory. Callers
(``artemis.crisis_content.poller``) must call this at most once per tick,
not once per card -- see that module's own test asserting the call count.
Deliberately NOT wired into the render path (``artemis.crisis_content.notify``)
for the same reason CCA12's author gave for leaving ``tab_id`` unpopulated:
a network dependency and a new failure mode on the hot notify path, for
every ``Ready`` transition, forever.

**Reuses ``writeback``'s tab walker and matcher -- does not re-derive
either.** ``artemis.crisis_content.writeback.locate_card_table`` already
walks every tab (recursing ``childTabs``) via ``_find_all_card_tables`` and
positively identifies the ONE live table matching a ``(header, copy_hash)``
pair, raising ``CardNotLocatedError`` on anything else -- see that
function's own docstring for why platform is not part of the match (a
Platform chip is opaque to ``documents.get`` in both directions; matching on
it was the CCA7 brief's own mistake, caught by a worker, and this module
must not repeat it). Calling ``locate_card_table`` again here, per card,
does NOT re-fetch -- it operates on the ``document`` dict this module
already fetched once; see ``resolve_card_tab_map`` below. ``_iter_tabs`` is
reused the same way, for the one thing ``locate_card_table`` does not
return: each tab's own title (needed to decide ``is_test``).

The HTTP fetch itself (``_fetch_document``) is a deliberate, independent
copy of ``writeback._fetch_document`` -- same GET, same
``includeTabsContent=true`` -- rather than an import. This mirrors the
established style in this package (``poller.py``'s ``_resolve_access_token``
mirrors, but does not import, ``routes/google_docs.py``'s
``_valid_access_token``; ``writeback.py``'s own credential resolution is a
third independent copy of the same shape, by the same reasoning given in its
own module docstring): the walker + matcher (the thing worth NOT
re-deriving, because getting header/hash matching subtly wrong is the
CCA7-class mistake) is imported; a small, self-contained HTTP GET is not.

**Failure handling.** ``resolve_card_tab_map`` raises ``TabResolutionError``
only when the ``documents.get`` call itself fails (network error, non-2xx
response, or a body that cannot be parsed as the expected JSON shape) --
callers MUST treat that the same as any other tick-level failure: notify
NOTHING this tick, alert Jon (debounced, via the poller's existing
``_enter_failure``), and let the next tick retry (see
``artemis.crisis_content.poller``). A card that fails to positively locate
in an otherwise-successfully-fetched document (``CardNotLocatedError`` for
just that one card -- e.g. a race between the HTML-export fetch and this
fetch, both against a live, externally-edited document) does NOT raise;
it is logged at ERROR and simply omitted from the returned map. Callers
must treat a missing map entry exactly like a resolution failure for that
one card: never guess ``is_test`` for it, never notify it this tick. See
``artemis.crisis_content.transitions.record_observation``'s ``tab_map``
parameter for where that per-card skip is enforced.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from artemis.config import settings
from artemis.crisis_content.models import ReviewCard
from artemis.crisis_content.writeback import (
    CardNotLocatedError,
    _iter_tabs,  # reuse of the existing recursive tab-flatten -- see module docstring
    locate_card_table,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CardTabInfo",
    "TabResolutionError",
    "resolve_card_tab_map",
]

_DOCS_API_BASE = "https://docs.googleapis.com/v1"

# The type of Transition.tab_id / ReviewCard.identity_key -- kept as a bare
# alias (not a NewType) so callers can pass a plain tuple without importing
# anything extra.
CardIdentityKey = tuple[str, str | None, int]


class TabResolutionError(Exception):
    """The one ``documents.get`` call for this tick failed or was unusable.

    Callers MUST notify nothing this tick, alert Jon, and let the next tick
    retry -- see the module docstring's "Failure handling" section. Never
    caught and silently downgraded to "treat as real": that is the exact
    outcome the CCA13 test lane exists to prevent (a test card posted to the
    live channel, the external vendor @-mentioned about a fake post).
    """


@dataclass(frozen=True)
class CardTabInfo:
    """Where one card's live table lives, and whether that makes it a test card."""

    tab_id: str
    tab_title: str
    is_test: bool


async def _fetch_document(access_token: str, document_id: str) -> dict[str, Any]:
    """GET ``documents.get`` with ``includeTabsContent=true``. Exactly one call.

    Independent copy of ``writeback._fetch_document`` -- see the module
    docstring for why this is a deliberate duplication, not an import.

    Wraps EVERYTHING that can go wrong (transport failure, a non-2xx status,
    or a 200 whose body is not the JSON object shape expected) into
    ``TabResolutionError`` -- the one exception ``resolve_card_tab_map``'s
    caller (``poller.py``) is set up to catch. ``httpx``'s own
    ``Response.json()`` raises a bare ``ValueError`` (not an
    ``httpx.HTTPError``) on a malformed body, which would otherwise escape
    uncaught past the poller's debounced failure handling -- see the module
    docstring's "Failure handling" section for why that must never happen.
    """
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            resp = await http.get(
                f"{_DOCS_API_BASE}/documents/{document_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "includeTabsContent": "true",
                    # MUST be explicit. The default
                    # (DEFAULT_FOR_CURRENT_ACCESS) renders suggestions INLINE for
                    # an editor, so the API text contains the reviewers' suggested
                    # insertions while the HTML export shows only accepted text.
                    # Card matching compares a copy hash across those two sources,
                    # so once Angela and Hannah suggested edits, 9 of 11 cards
                    # stopped matching and the pipeline skipped every one of them
                    # (production, 2026-08-12). This mode returns accepted text,
                    # which is what the export shows.
                    "suggestionsViewMode": "PREVIEW_WITHOUT_SUGGESTIONS",
                },
            )
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPError as exc:
        raise TabResolutionError(f"documents.get failed: {exc}") from exc
    except ValueError as exc:
        raise TabResolutionError(f"documents.get returned an unparseable body: {exc}") from exc
    if not isinstance(payload, dict):
        raise TabResolutionError(
            f"documents.get returned a {type(payload).__name__}, not a JSON object"
        )
    return payload


def _tab_titles(document: dict[str, Any]) -> dict[str, str]:
    """``tab_id -> title`` for every tab in ``document`` (recursing ``childTabs``).

    Reuses ``writeback._iter_tabs`` (the existing recursive flatten) rather
    than re-deriving the ``childTabs`` recursion -- see the module docstring.
    ``locate_card_table`` / ``_find_all_card_tables`` never return the title,
    only the ``tab_id``, which is the one gap this fills.
    """
    raw_tabs = document.get("tabs")
    if not isinstance(raw_tabs, list):
        return {}
    titles: dict[str, str] = {}
    for tab in _iter_tabs(raw_tabs):
        tab_properties = tab.get("tabProperties")
        if not isinstance(tab_properties, dict):
            continue
        tab_id = tab_properties.get("tabId")
        title = tab_properties.get("title")
        if tab_id and isinstance(title, str):
            titles[str(tab_id)] = title
    return titles


def _is_test_title(tab_title: str) -> bool:
    """True iff ``tab_title`` contains ``settings.crisis_content_test_tab_marker``.

    Case-insensitive substring match -- see that setting's own docstring. A
    tab with no marker in its title (e.g. a brand-new monthly tab) is a real
    tab with no configuration change required.
    """
    marker = settings.crisis_content_test_tab_marker.strip()
    if not marker:
        return False
    return marker.lower() in tab_title.lower()


async def resolve_card_tab_map(
    access_token: str,
    document_id: str,
    cards: Sequence[ReviewCard],
) -> Mapping[CardIdentityKey, CardTabInfo]:
    """One ``documents.get`` call, then resolve every card's tab in memory.

    Returns a map keyed by ``ReviewCard.identity_key`` (the same identity
    ``transitions.py`` already uses everywhere else). A card that cannot be
    positively located (``CardNotLocatedError`` -- ambiguous header+hash
    match, or no match at all, e.g. a race against a live, externally-edited
    document) is logged at ERROR and simply absent from the returned map --
    never guessed at. See the module docstring's "Failure handling" section
    for the full contract, including what raises ``TabResolutionError``
    (only the fetch itself) versus what is silently omitted (a single card's
    match failure).

    Matching is header text + copy-body hash, never platform -- delegated
    entirely to ``writeback.locate_card_table``, which already implements
    this (a Platform chip is invisible to ``documents.get`` in both
    directions; see that function's docstring and the CCA7 brief's own
    mistake, caught by a worker, of matching on it).
    """
    try:
        document = await _fetch_document(access_token, document_id)
    except TabResolutionError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        # Belt-and-braces: ``_fetch_document`` already wraps these itself
        # (see its own docstring), but a caller/test that swaps in its own
        # ``_fetch_document`` and raises a raw ``httpx`` or JSON error must
        # still surface as ``TabResolutionError`` -- that is the ONE
        # exception callers are set up to catch.
        raise TabResolutionError(f"documents.get failed: {exc}") from exc

    tab_titles = _tab_titles(document)

    tab_map: dict[CardIdentityKey, CardTabInfo] = {}
    for card in cards:
        try:
            location, _total_count = locate_card_table(
                document,
                header=card.header,
                copy_hash=card.copy_hash,
                # Position among identical siblings, used only when header +
                # copy hash is ambiguous -- which happens for a genuinely
                # duplicated card (Jon's TESTING-tab copy, and the vendor's own
                # repeated headers across platform variants).
                ordinal=card.identity_key[2],
            )
        except CardNotLocatedError as exc:
            logger.error(
                "crisis_content: could not positively locate a live tab for "
                "card=%r (header=%r) -- omitting it from this tick's tab map. "
                "Callers must skip notifying this card, not guess; the next "
                "tick will retry. %s",
                card.identity_key,
                card.header,
                exc,
            )
            continue
        title = tab_titles.get(location.tab_id, "")
        tab_map[card.identity_key] = CardTabInfo(
            tab_id=location.tab_id,
            tab_title=title,
            is_test=_is_test_title(title),
        )
    return tab_map
