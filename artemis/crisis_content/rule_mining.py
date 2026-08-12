"""CCA15 -- mine repeated Google Docs suggestions into candidate style rules.

Background: ``docs/crisis-content-approval-pipeline.md`` and
``briefs/cca15-mine-suggestions-into-rules.md``. Angela and Hannah reviewed
every one of the vendor's posts in Google Docs Suggesting mode. Their edits
show ``child -> student`` three times and ``kids -> students`` once -- not
four one-off judgments but one house style rule revealing itself. This
module turns that kind of repetition into a candidate row in
``writing_training_candidates``, the existing human-reviewed queue Angela
already uses (38 pending as of 2026-08-11). It never writes ``writing_rules``
directly, and it never writes anything at all to the Google Doc.

**Read-only against the document, always.** This module only ever calls
``documents.get`` (a GET). No write, no comment, no accept/reject of a
suggestion -- there is no function anywhere in this file capable of any of
those.

**Fetch mechanics (verified against the live doc, do not re-derive).**
``includeTabsContent=true`` AND ``suggestionsViewMode=SUGGESTIONS_INLINE``
are both required. Suggestion markers live on the **textRun**, not the
paragraph element:

    tabs[].documentTab.body.content[].table.tableRows[].tableCells[]
        .content[].paragraph.elements[].textRun
            -> suggestedInsertionIds / suggestedDeletionIds

An earlier probe checked one level too high (the paragraph element itself)
and found zero suggestions on a document that has 144 of them --
``extract_suggestion_pairs`` only ever reads ``element["textRun"]``, never
the element's own top-level keys, precisely to not repeat that mistake (see
the "fixture with markers only on the element" test in
``tests/test_crisis_content_rule_mining.py``).

**Independent fetch, by design.** ``artemis.crisis_content.tab_resolution``
already makes its own ``documents.get`` call with
``suggestionsViewMode=PREVIEW_WITHOUT_SUGGESTIONS`` (it wants accepted text,
to match the HTML export's card-matching hash). This module needs the
opposite -- suggestions inline -- so it cannot share that call, and per this
package's established style (see ``tab_resolution.py``'s own module
docstring on why its HTTP GET is a deliberate copy of ``writeback.py``'s,
not an import) a second small, self-contained HTTP GET is duplicated here
rather than parameterizing an existing one. This also keeps this slice at
zero import coupling to any file it was told not to touch, while a sibling
slice edits this package concurrently.

**A replacement is the whole contiguous span of suggestion activity, not the
run Google Docs happened to store it in (CCA16).** CCA15 shipped mining at
run level: a maximal consecutive run of DEL-only textRuns paired with an
immediately-following maximal run of ADD-only textRuns. Against Jen's real
doc that produced pairs like ``It's a`` -> ``students`` and ``can't`` ->
`` or `` -- fragments of one sentence Angela rewrote in place, sliced
wherever Google's diff happened to put a run boundary and then paired with
whatever fragment landed next to it. CCA15's 15 tests never caught this
because they only ever seeded a single-word swap (``child`` -> ``student``),
which is a one-block-each-side cluster either way -- the tests were not
wrong, the assumption that production edits look like the fixtures was.

``_paragraph_pairs`` now coalesces at the *cluster* level, via
``_cluster_blocks``: a maximal consecutive stretch of DEL/ADD blocks,
unbroken by an untouched (``none``) or ambiguous (``both``) run, is one
cluster, and each cluster yields at most one pair -- every DEL block's text
in the cluster, concatenated in document order, against every ADD block's
text, concatenated in document order (no separator inserted between
blocks -- see ``_paragraph_pairs``'s docstring for the cosmetic
consequence). A cluster made of only one kind -- all DEL (a whole-paragraph
deletion, or a cut), all ADD, or a DEL block adjacent to an ADD block whose
concatenated text is empty (a Docs boundary-marker artifact, seen live as
``how it's made.`` -> "") -- yields no pair, exactly as a lone deletion
always has.

**A cluster this long is not a rule, it is one edit (CCA16).** Coalescing
fixes the unit but not a long span recurring verbatim (pasted boilerplate).
``record_and_propose`` therefore refuses to *propose* -- it still counts --
any pair whose longer side exceeds
``settings.crisis_content_rule_mining_max_words`` words (default 6). A
standing house rule is guidance a writer can hold in their head; "prefer X
over Y" where X is a whole rewritten sentence is one edit, not a rule, and
Angela's review queue is not where one edit belongs.

**Never auto-applies anything.** ``record_and_propose`` is the only
function that writes to the database, and the only table it ever writes
outside this module's own two tables
(``crisis_content_rule_mining_observations``,
``crisis_content_rule_mining_pairs``) is ``writing_training_candidates``,
via ``artemis.writing_rules.repository.create_training_candidate`` -- the
existing repository function the human-review UI already reads from. It
never touches ``writing_rules``, ``writing_examples``, or any existing
``crisis_content_*`` table.

**Attribution.** Confirmed against Google's own documentation (Work with
suggestions, developers.google.com/workspace/docs/api/how-tos/suggestions):
the Docs API exposes suggestion IDs, never an author name, email, or user
id, for either ``suggestedInsertionIds`` or ``suggestedDeletionIds``. There
is no clean way to resolve who suggested a given edit from
``documents.get``, so every pair recorded here carries no author -- see
``SuggestionPair`` below, which has no author field at all rather than a
field that would always be ``None``.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.crisis_content.rule_mining_orm import (
    CrisisContentRuleMiningObservation,
    CrisisContentRuleMiningPair,
)
from artemis.writing_rules.models import WritingTrainingCandidate
from artemis.writing_rules.repository import create_training_candidate

logger = logging.getLogger(__name__)

__all__ = [
    "MiningRunResult",
    "RuleMiningFetchError",
    "SuggestionPair",
    "extract_suggestion_pairs",
    "fetch_document_with_suggestions",
    "is_noise_pair",
    "normalize_for_aggregation",
    "record_and_propose",
]

_DOCS_API_BASE = "https://docs.googleapis.com/v1"

# Curly/smart quote and apostrophe variants -> their straight ASCII
# equivalent, for the typography-artifact filter only (never used for the
# aggregation key -- see ``normalize_for_aggregation`` vs ``is_noise_pair``).
_SMART_QUOTE_TRANSLATION = str.maketrans(
    {
        "‘": "'",  # left single quotation mark
        "’": "'",  # right single quotation mark / apostrophe
        "‚": "'",  # single low-9 quotation mark
        "“": '"',  # left double quotation mark
        "”": '"',  # right double quotation mark
        "„": '"',  # double low-9 quotation mark
    }
)


class RuleMiningFetchError(Exception):
    """The ``documents.get`` call for suggestion mining failed or was unusable.

    Callers should treat this exactly like any other tick-level fetch
    failure elsewhere in this package (log, alert, retry next time) --
    never guess at suggestions from a partial or absent response.
    """


@dataclass(frozen=True)
class SuggestionPair:
    """One (deleted, inserted) pair extracted from a suggestion cluster (CCA16).

    "Cluster" here means the whole contiguous span of DEL/ADD suggestion
    activity, coalesced by ``_paragraph_pairs`` -- not a single adjacent
    run pair, which is what this carried pre-CCA16. Deliberately carries no
    author field -- see the module docstring's
    "Attribution" section. ``is_test_tab`` and ``card_header`` are carried
    through from extraction so ``record_and_propose`` can filter/cite
    without a second document walk.
    """

    deleted_text: str
    inserted_text: str
    deletion_ids: tuple[str, ...]
    insertion_ids: tuple[str, ...]
    tab_id: str
    tab_title: str
    is_test_tab: bool
    card_header: str


@dataclass(frozen=True)
class MiningRunResult:
    """What one call to ``record_and_propose`` did, for callers/reports.

    ``held_by_length_guard`` (CCA16) counts pairs that reached the proposal
    threshold on this call but were withheld because their longer side
    exceeds ``max_words``/``settings.crisis_content_rule_mining_max_words``
    -- they were still counted normally; they are simply never proposed
    while over the guard. See ``record_and_propose``'s "length guard"
    section.
    """

    new_observations: int
    skipped_test_tab: int
    skipped_noise: int
    proposed_candidates: tuple[WritingTrainingCandidate, ...]
    held_by_length_guard: int = 0


# ── Fetch ─────────────────────────────────────────────────────────────────────


async def fetch_document_with_suggestions(access_token: str, document_id: str) -> dict[str, Any]:
    """GET ``documents.get`` with suggestions rendered inline. Exactly one call.

    ``includeTabsContent=true`` AND ``suggestionsViewMode=SUGGESTIONS_INLINE``
    are both required -- see the module docstring's "Fetch mechanics"
    section. Wraps every failure mode (transport error, non-2xx, unparseable
    body, or a parsed-but-non-object body) into ``RuleMiningFetchError``.
    """
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            resp = await http.get(
                f"{_DOCS_API_BASE}/documents/{document_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "includeTabsContent": "true",
                    "suggestionsViewMode": "SUGGESTIONS_INLINE",
                },
            )
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPError as exc:
        raise RuleMiningFetchError(f"documents.get failed: {exc}") from exc
    except ValueError as exc:
        raise RuleMiningFetchError(f"documents.get returned an unparseable body: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuleMiningFetchError(
            f"documents.get returned a {type(payload).__name__}, not a JSON object"
        )
    return payload


# ── Pure extraction (no network, no DB) ───────────────────────────────────────


def _is_test_tab_title(tab_title: str) -> bool:
    """Independent copy of the CCA13 test-tab check (settings-driven).

    Reimplemented here rather than imported from
    ``artemis.crisis_content.tab_resolution`` so this module has zero import
    coupling to a file it was told not to touch, while a sibling slice edits
    this package concurrently. It is one line of logic re-derived from the
    same setting (``settings.crisis_content_test_tab_marker``), not a
    parallel policy -- see that setting's own docstring in
    ``artemis/config.py`` for what the marker means.
    """
    marker = settings.crisis_content_test_tab_marker.strip()
    if not marker:
        return False
    return marker.lower() in tab_title.lower()


def _iter_tabs(tabs: Sequence[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
    """Flatten ``tabs[]`` recursing ``childTabs``, depth-first."""
    for tab in tabs:
        if not isinstance(tab, Mapping):
            continue
        yield tab
        child_tabs = tab.get("childTabs")
        if isinstance(child_tabs, list):
            yield from _iter_tabs(child_tabs)


def _paragraph_text(paragraph: Mapping[str, Any]) -> str:
    parts: list[str] = []
    elements = paragraph.get("elements")
    if not isinstance(elements, list):
        return ""
    for element in elements:
        if not isinstance(element, Mapping):
            continue
        text_run = element.get("textRun")
        if isinstance(text_run, Mapping):
            content = text_run.get("content")
            if isinstance(content, str):
                parts.append(content)
    return "".join(parts)


def _cell_text(cell: Mapping[str, Any]) -> str:
    parts: list[str] = []
    content = cell.get("content")
    if not isinstance(content, list):
        return ""
    for item in content:
        if not isinstance(item, Mapping):
            continue
        paragraph = item.get("paragraph")
        if isinstance(paragraph, Mapping):
            parts.append(_paragraph_text(paragraph))
    return "\n".join(parts)


def _table_text(table: Mapping[str, Any]) -> str:
    parts: list[str] = []
    rows = table.get("tableRows")
    if not isinstance(rows, list):
        return ""
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        cells = row.get("tableCells")
        if not isinstance(cells, list):
            continue
        for cell in cells:
            if isinstance(cell, Mapping):
                parts.append(_cell_text(cell))
    return "\n".join(parts)


def _table_header(table: Mapping[str, Any]) -> str:
    """Best-effort card header: row0/cell0's text, first line only.

    Mirrors the design doc's "row0.cell0 -> header line" rule (parser.py's
    HTML-side equivalent), re-derived here against the JSON tree purely for
    citing evidence in a rationale -- this is NOT a card-identity key and
    feeds nothing in ``transitions.py``. Returns "" if the table is empty or
    unusually shaped rather than raising; a missing header degrades the
    rationale's citation, not the pipeline.
    """
    rows = table.get("tableRows")
    if not isinstance(rows, list) or not rows:
        return ""
    first_row = rows[0]
    if not isinstance(first_row, Mapping):
        return ""
    cells = first_row.get("tableCells")
    if not isinstance(cells, list) or not cells:
        return ""
    first_cell = cells[0]
    if not isinstance(first_cell, Mapping):
        return ""
    text = _cell_text(first_cell).strip()
    if not text:
        return ""
    return text.splitlines()[0]


def _is_review_card_table(table_text: str) -> bool:
    """Independent copy of the doc's card signature.

    See ``docs/crisis-content-approval-pipeline.md``, "Card identity": "A
    card is a <table> whose text contains both 'Platform:' and 'Copy
    review'." Re-derived here against the JSON tree (parser.py/writeback.py
    own the HTML-export version and are out of scope for this slice) so
    rule mining only ever looks at the vendor's actual review cards, not
    the Strategy Plan / Content Plan Draft / Repeatable Framework tables
    that live on other tabs of the same doc.
    """
    return "Platform:" in table_text and "Copy review" in table_text


def _classify_run(text_run: Mapping[str, Any]) -> str:
    """ "del" | "add" | "both" | "none" for one textRun's suggestion markers.

    "both" (a run carrying both suggestedDeletionIds AND
    suggestedInsertionIds -- overlapping suggestions from two people) is
    deliberately its own case, treated as a boundary that cannot join a
    pair on either side: guessing which side it belongs to would be exactly
    the kind of synthesis the brief warns against.
    """
    has_del = bool(text_run.get("suggestedDeletionIds"))
    has_add = bool(text_run.get("suggestedInsertionIds"))
    if has_del and has_add:
        return "both"
    if has_del:
        return "del"
    if has_add:
        return "add"
    return "none"


@dataclass
class _Block:
    kind: str  # "del" or "add"
    text: str
    ids: tuple[str, ...]
    start: int
    end: int  # exclusive, element index


def _paragraph_blocks(paragraph: Mapping[str, Any]) -> list[_Block]:
    """Merge consecutive same-kind textRuns into DEL/ADD blocks, in order.

    A run with neither marker (kind "none") or an ambiguous overlap (kind
    "both") always terminates the current block -- it is untouched
    context, or unresolvable, either way not part of a pair.
    """
    elements = paragraph.get("elements")
    if not isinstance(elements, list):
        return []

    blocks: list[_Block] = []
    current_kind: str | None = None
    current_text: list[str] = []
    current_ids: list[str] = []
    current_start = 0

    def _flush(end_idx: int) -> None:
        nonlocal current_kind, current_text, current_ids
        if current_kind in ("del", "add") and current_text:
            blocks.append(
                _Block(
                    kind=current_kind,
                    text="".join(current_text),
                    ids=tuple(dict.fromkeys(current_ids)),
                    start=current_start,
                    end=end_idx,
                )
            )
        current_kind = None
        current_text = []
        current_ids = []

    for idx, element in enumerate(elements):
        text_run = element.get("textRun") if isinstance(element, Mapping) else None
        if not isinstance(text_run, Mapping):
            _flush(idx)
            continue
        kind = _classify_run(text_run)
        content = text_run.get("content")
        content_str = content if isinstance(content, str) else ""
        if kind not in ("del", "add"):
            _flush(idx)
            continue
        ids_key = "suggestedDeletionIds" if kind == "del" else "suggestedInsertionIds"
        run_ids = [i for i in (text_run.get(ids_key) or []) if isinstance(i, str)]
        if kind == current_kind:
            current_text.append(content_str)
            current_ids.extend(run_ids)
        else:
            _flush(idx)
            current_kind = kind
            current_text = [content_str]
            current_ids = list(run_ids)
            current_start = idx

    _flush(len(elements))
    return blocks


def _cluster_blocks(blocks: Sequence[_Block]) -> list[list[_Block]]:
    """Group ``blocks`` into maximal runs of mutually adjacent blocks (CCA16).

    Two blocks join the same cluster iff nothing separates them: the
    earlier block's ``end`` element index equals the later block's
    ``start``. Any gap ends the cluster -- and there is always a gap where
    an untouched (``none``) or ambiguous (``both``) run sat, because
    ``_paragraph_blocks`` never emits a block for one of those, so the
    index range it occupied is simply missing from the sequence. This is
    the same adjacency test CCA15's ``_paragraph_pairs`` applied between
    exactly two blocks; CCA16 applies it across the whole run of blocks so
    an interleaved DEL/ADD/DEL/ADD rewrite becomes one cluster instead of
    several independently-paired fragments.
    """
    clusters: list[list[_Block]] = []
    for block in blocks:
        if clusters and clusters[-1][-1].end == block.start:
            clusters[-1].append(block)
        else:
            clusters.append([block])
    return clusters


def _paragraph_pairs(
    paragraph: Mapping[str, Any],
) -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    """Every (deleted, inserted) span-level pair in one paragraph (CCA16).

    Coalesces each maximal cluster of contiguous DEL/ADD blocks (see
    ``_cluster_blocks``) into at most one pair: every DEL block's text in
    the cluster, concatenated in document order, against every ADD block's
    text, concatenated in document order. Concatenation is literal -- no
    separator is inserted between blocks -- because the brief's contract is
    "the deleted text" and "the inserted text" as two spans to store
    verbatim, not a reconstructed sentence; the one cosmetic consequence is
    that two fragments with no whitespace between them (e.g. a deleted
    ``"the"`` immediately followed in the cluster by a deleted ``"topic"``,
    with an insertion in between) can fuse into what reads as a single
    non-word when displayed. See this slice's report for that tradeoff.

    A cluster made of only one kind -- all DEL, all ADD, or a DEL block
    adjacent to an ADD block whose concatenated text is empty (the live
    ``how it's made.`` -> "" artifact) -- yields no pair: the ``not
    deleted_text or not inserted_text`` guard below is deliberately a
    truthiness check, not an "ADD block exists" check, so an empty-content
    ADD block is treated exactly like no ADD block at all.
    """
    clusters = _cluster_blocks(_paragraph_blocks(paragraph))
    pairs: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = []
    for cluster in clusters:
        deleted_text = "".join(block.text for block in cluster if block.kind == "del")
        inserted_text = "".join(block.text for block in cluster if block.kind == "add")
        if not deleted_text or not inserted_text:
            continue
        deletion_ids = tuple(
            dict.fromkeys(id_ for block in cluster if block.kind == "del" for id_ in block.ids)
        )
        insertion_ids = tuple(
            dict.fromkeys(id_ for block in cluster if block.kind == "add" for id_ in block.ids)
        )
        pairs.append((deleted_text, inserted_text, deletion_ids, insertion_ids))
    return pairs


def extract_suggestion_pairs(document: Mapping[str, Any]) -> list[SuggestionPair]:
    """Pure extraction: every qualifying (deleted, inserted) pair in ``document``.

    Read-only over the ``documents.get`` JSON tree returned by
    ``fetch_document_with_suggestions``. Walks every tab (flattening
    ``childTabs``), keeps only tables matching the review-card signature
    (``_is_review_card_table``), and returns one ``SuggestionPair`` per
    adjacent DEL->ADD run pair found inside that table's cells.

    Does NOT filter typography/whitespace noise and does NOT apply the
    proposal threshold -- that is ``record_and_propose``'s job, so this
    function stays trivially testable against the raw JSON shape alone.
    """
    tabs = document.get("tabs")
    if not isinstance(tabs, list):
        return []

    pairs: list[SuggestionPair] = []
    for tab in _iter_tabs(tabs):
        tab_properties = tab.get("tabProperties")
        tab_id = ""
        tab_title = ""
        if isinstance(tab_properties, Mapping):
            raw_tab_id = tab_properties.get("tabId")
            if raw_tab_id:
                tab_id = str(raw_tab_id)
            raw_title = tab_properties.get("title")
            if isinstance(raw_title, str):
                tab_title = raw_title
        is_test = _is_test_tab_title(tab_title)

        document_tab = tab.get("documentTab")
        body = document_tab.get("body") if isinstance(document_tab, Mapping) else None
        content = body.get("content") if isinstance(body, Mapping) else None
        if not isinstance(content, list):
            continue

        for structural_element in content:
            if not isinstance(structural_element, Mapping):
                continue
            table = structural_element.get("table")
            if not isinstance(table, Mapping):
                continue
            table_text = _table_text(table)
            if not _is_review_card_table(table_text):
                continue
            header = _table_header(table) or "(untitled card)"

            rows = table.get("tableRows")
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                cells = row.get("tableCells")
                if not isinstance(cells, list):
                    continue
                for cell in cells:
                    if not isinstance(cell, Mapping):
                        continue
                    cell_content = cell.get("content")
                    if not isinstance(cell_content, list):
                        continue
                    for item in cell_content:
                        if not isinstance(item, Mapping):
                            continue
                        paragraph = item.get("paragraph")
                        if not isinstance(paragraph, Mapping):
                            continue
                        for deleted, inserted, del_ids, add_ids in _paragraph_pairs(paragraph):
                            pairs.append(
                                SuggestionPair(
                                    deleted_text=deleted,
                                    inserted_text=inserted,
                                    deletion_ids=del_ids,
                                    insertion_ids=add_ids,
                                    tab_id=tab_id,
                                    tab_title=tab_title,
                                    is_test_tab=is_test,
                                    card_header=header,
                                )
                            )
    return pairs


# ── Normalization / noise filtering (pure) ────────────────────────────────────


def normalize_for_aggregation(text: str) -> str:
    """Casefold + collapse whitespace -- the pair-identity comparison key.

    Deliberately does NOT unify smart/straight quotes (that is
    ``is_noise_pair``'s job, applied before this is ever used) -- this is
    the aggregation key for genuinely different pairs, not the typography
    filter. "Case is preserved for display but not for aggregation": this
    function is "not for aggregation"'s other half; display text is carried
    separately (``SuggestionPair.deleted_text``/``inserted_text``,
    ``CrisisContentRuleMiningPair.display_deleted``/``display_inserted``).
    """
    return " ".join(text.split()).casefold()


def _normalize_typography(text: str) -> str:
    return text.translate(_SMART_QUOTE_TRANSLATION)


def is_noise_pair(deleted_text: str, inserted_text: str) -> bool:
    """True iff the only difference is whitespace and/or a smart-quote swap.

    The live doc's ``"'s" -> "'s"`` pair (straight apostrophe autocorrected
    to a curly one) is Docs autocorrect, not an editorial decision; a
    whitespace-only difference is the same kind of nothing. Both would be
    pure noise in Angela's review queue -- see the module and brief
    docstrings' "Filter typography artifacts" section. Compares with
    quote-unification AND whitespace-collapse together, so either artifact
    (or both at once) is caught.
    """
    d = " ".join(_normalize_typography(deleted_text).split()).casefold()
    i = " ".join(_normalize_typography(inserted_text).split()).casefold()
    return d == i


def _word_count(text: str) -> int:
    """Whitespace-separated word count, for the CCA16 length guard.

    Deliberately the same primitive ``str.split()`` already uses elsewhere
    in this file (``normalize_for_aggregation``, ``is_noise_pair``) rather
    than a separate tokenizer -- see ``record_and_propose``'s "length
    guard" section for why this exists and
    ``settings.crisis_content_rule_mining_max_words`` for the default and
    rationale.
    """
    return len(text.split())


def _occurrence_key(pair: SuggestionPair) -> str:
    """Stable dedup key for one physical suggestion occurrence.

    Docs suggestion ids are globally unique per suggestion, so the ids
    alone already identify "the same suggestion seen again on a later
    poll" -- re-fetching a still-pending suggestion reproduces the same
    ids, and this key, exactly. Tab/header/text are appended only as a
    defensive tie-breaker in case a future API response ever omits ids for
    a run (never observed, but cheap to guard).
    """
    raw = "|".join(
        [
            ",".join(sorted(pair.deletion_ids)),
            ",".join(sorted(pair.insertion_ids)),
            pair.tab_id,
            pair.card_header,
            pair.deleted_text,
            pair.inserted_text,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Persistence + proposal (the only DB-writing code in this module) ─────────


async def _get_or_create_pair(
    session: AsyncSession,
    *,
    normalized_deleted: str,
    normalized_inserted: str,
    display_deleted: str,
    display_inserted: str,
) -> CrisisContentRuleMiningPair:
    result = await session.execute(
        select(CrisisContentRuleMiningPair).where(
            CrisisContentRuleMiningPair.normalized_deleted == normalized_deleted,
            CrisisContentRuleMiningPair.normalized_inserted == normalized_inserted,
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = CrisisContentRuleMiningPair(
        normalized_deleted=normalized_deleted,
        normalized_inserted=normalized_inserted,
        display_deleted=display_deleted,
        display_inserted=display_inserted,
        occurrence_count=0,
        status="counting",
    )
    session.add(row)
    await session.flush()
    return row


async def _propose_candidate(
    session: AsyncSession,
    pair_row: CrisisContentRuleMiningPair,
    example_headers: Sequence[str],
) -> WritingTrainingCandidate:
    """INSERT one ``writing_training_candidates`` row and flip the pair to proposed.

    Never touches ``writing_rules`` -- this is a proposal for Angela's
    existing human review gate, exactly like every other row already in
    that table. ``profile_id=None`` (unscoped): the existing
    ``/training-candidates`` list route and the Writing Studio dashboard
    both already default to no profile filter, so an unscoped candidate
    surfaces in the same queue Angela already reviews without this module
    guessing which numeric profile id is "hers" in a given environment. See
    this worker's report for the deliberate judgment call this is.
    """
    proposed_text = f'Prefer "{pair_row.display_inserted}" over "{pair_row.display_deleted}".'

    seen_headers: list[str] = []
    for header in example_headers:
        if header and header not in seen_headers:
            seen_headers.append(header)
    examples_note = f" Example cards: {', '.join(seen_headers[:5])}." if seen_headers else ""

    rationale = (
        f"Mined from {pair_row.occurrence_count} Google Docs suggestion edits in the "
        f'crisis-content vendor doc changing "{pair_row.display_deleted}" to '
        f'"{pair_row.display_inserted}" (CCA15 rule mining, threshold '
        f"{settings.crisis_content_rule_mining_threshold}).{examples_note} Suggestion "
        "authorship is not exposed by the Docs API (documents.get returns suggestion "
        "ids, never an author), so no individual reviewer is attributed."
    )

    candidate = await create_training_candidate(
        session,
        profile_id=None,
        draft_id=None,
        candidate_type="rule",
        proposed_text=proposed_text,
        rationale=rationale,
        status="proposed",
    )
    pair_row.status = "proposed"
    pair_row.proposed_candidate_id = candidate.id
    await session.flush()
    logger.info(
        "crisis_content.rule_mining: proposed candidate id=%s for pair %r -> %r "
        "(count=%s, threshold=%s)",
        candidate.id,
        pair_row.display_deleted,
        pair_row.display_inserted,
        pair_row.occurrence_count,
        settings.crisis_content_rule_mining_threshold,
    )
    return candidate


async def record_and_propose(
    session: AsyncSession,
    pairs: Sequence[SuggestionPair],
    *,
    threshold: int | None = None,
    max_words: int | None = None,
) -> MiningRunResult:
    """Persist new suggestion occurrences, accumulate counts, propose at threshold.

    Contract (see the CCA15 brief's "Hard constraints" and "Tests", and
    CCA16's length-guard addition):

    - Test-tab pairs (``SuggestionPair.is_test_tab``) contribute NOTHING --
      no observation row, no count, no candidate.
    - Typography/whitespace-only pairs (``is_noise_pair``) contribute
      NOTHING, same as above.
    - Idempotent: a pair already recorded under its ``occurrence_key`` (the
      same physical suggestion, seen again on a later poll) is skipped --
      never double-counted.
    - Counts accumulate in ``crisis_content_rule_mining_pairs`` across
      separate calls (separate poll ticks), because they are read from the
      DB before being incremented, never reset per call.
    - A pair proposes a candidate exactly once: the moment its count first
      reaches ``threshold`` (default ``settings.crisis_content_rule_mining_
      threshold``), its aggregate row flips to ``status="proposed"`` and is
      never proposed again even as its count keeps climbing.
    - **Length guard (CCA16).** A pair whose longer side exceeds
      ``max_words`` (default ``settings.crisis_content_rule_mining_
      max_words``) in whitespace-separated words is counted exactly like
      any other pair, but is never proposed, no matter how high its count
      climbs -- see ``MiningRunResult.held_by_length_guard``. Coalescing
      (``_paragraph_pairs``) fixes the *unit* mining operates on; this
      guard is the independent safety net for a long span that genuinely
      recurs (e.g. boilerplate pasted into several cards) rather than
      relying on "long spans are usually unique" alone.
    - The only table written outside this module's own two is
      ``writing_training_candidates`` (via ``create_training_candidate``).
      ``writing_rules`` is never written -- see this module's docstring.

    Commits at the end (mirrors ``decisions.record_decision``'s "commits
    before returning" convention elsewhere in this package) -- this is the
    terminal write of one mining run.
    """
    active_threshold = (
        threshold if threshold is not None else settings.crisis_content_rule_mining_threshold
    )
    active_max_words = (
        max_words if max_words is not None else settings.crisis_content_rule_mining_max_words
    )

    new_observations = 0
    skipped_test_tab = 0
    skipped_noise = 0
    held_by_length_guard = 0
    proposed: list[WritingTrainingCandidate] = []
    example_headers_by_pair: dict[tuple[str, str], list[str]] = {}

    for pair in pairs:
        if pair.is_test_tab:
            skipped_test_tab += 1
            continue
        if is_noise_pair(pair.deleted_text, pair.inserted_text):
            skipped_noise += 1
            continue

        occurrence_key = _occurrence_key(pair)
        existing = await session.execute(
            select(CrisisContentRuleMiningObservation.id).where(
                CrisisContentRuleMiningObservation.occurrence_key == occurrence_key
            )
        )
        if existing.scalar_one_or_none() is not None:
            # Same physical suggestion, already recorded on an earlier poll.
            continue

        normalized_deleted = normalize_for_aggregation(pair.deleted_text)
        normalized_inserted = normalize_for_aggregation(pair.inserted_text)

        observation = CrisisContentRuleMiningObservation(
            occurrence_key=occurrence_key,
            normalized_deleted=normalized_deleted,
            normalized_inserted=normalized_inserted,
            deleted_text=pair.deleted_text,
            inserted_text=pair.inserted_text,
            tab_id=pair.tab_id or None,
            tab_title=pair.tab_title or None,
            card_header=pair.card_header or None,
        )
        session.add(observation)
        new_observations += 1

        pair_row = await _get_or_create_pair(
            session,
            normalized_deleted=normalized_deleted,
            normalized_inserted=normalized_inserted,
            display_deleted=pair.deleted_text,
            display_inserted=pair.inserted_text,
        )
        pair_row.occurrence_count += 1
        pair_row.last_seen_at = datetime.now(UTC)

        pair_key = (normalized_deleted, normalized_inserted)
        example_headers_by_pair.setdefault(pair_key, []).append(pair.card_header)

        if pair_row.occurrence_count >= active_threshold and pair_row.status != "proposed":
            if (
                _word_count(pair_row.display_deleted) > active_max_words
                or _word_count(pair_row.display_inserted) > active_max_words
            ):
                held_by_length_guard += 1
                logger.info(
                    "crisis_content.rule_mining: withholding proposal for pair %r -> %r "
                    "(count=%s >= threshold=%s) -- exceeds the %s-word length guard (CCA16)",
                    pair_row.display_deleted,
                    pair_row.display_inserted,
                    pair_row.occurrence_count,
                    active_threshold,
                    active_max_words,
                )
            else:
                candidate = await _propose_candidate(
                    session, pair_row, example_headers_by_pair.get(pair_key, [])
                )
                proposed.append(candidate)

    await session.commit()
    return MiningRunResult(
        new_observations=new_observations,
        skipped_test_tab=skipped_test_tab,
        skipped_noise=skipped_noise,
        proposed_candidates=tuple(proposed),
        held_by_length_guard=held_by_length_guard,
    )
