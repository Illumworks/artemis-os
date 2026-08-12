"""HTML export -> ``list[ReviewCard]``. Pure functions only: no network, no DB.

Background: ``docs/crisis-content-approval-pipeline.md``. The short version --
the doc's status/platform values are Google Docs dropdown "chips", which the
Docs API cannot read at all (a chip comes back as a bare index range with no
content). The HTML export endpoint renders chip values as plain text instead,
which is what this module parses. That fetch lives in ``export_client.py``;
this module never imports it and never imports ``httpx`` -- it only knows how
to turn an HTML string into cards.

Two exceptions live here (``SignInPageError``, ``NoReviewCardsFoundError``)
rather than in ``export_client.py``, even though the sign-in-page check is
conceptually part of "the fetch". Reasons:

1. ``parse_review_cards`` must be able to raise ``SignInPageError`` on its
   own when handed sign-in-page HTML directly (that's how it's unit tested,
   and it's also the only way to make "zero cards" and "not actually a doc"
   distinguishable failure modes at the parser boundary).
2. Keeping the predicate + exception here means ``export_client.py`` can
   reuse the exact same check to fail fast right after fetching, without
   ``parser.py`` ever importing ``httpx``.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from artemis.crisis_content.models import ReviewCard, StatusClassification

logger = logging.getLogger(__name__)

__all__ = [
    "CrisisContentParseError",
    "SignInPageError",
    "NoReviewCardsFoundError",
    "looks_like_sign_in_page",
    "unwrap_google_redirect_url",
    "classify_status",
    "parse_review_cards",
]


class CrisisContentParseError(Exception):
    """Base class for crisis-content parsing failures."""


class SignInPageError(CrisisContentParseError):
    """Raised when the HTML handed to the parser is a Google sign-in page.

    A 200 response whose body is a sign-in page is a failure, not a document
    -- the access token is invalid/expired. This must never be swallowed
    into an empty card list, because an empty list is indistinguishable from
    "no work to do right now".
    """


class NoReviewCardsFoundError(CrisisContentParseError):
    """Raised when the HTML parses structurally but yields zero review cards.

    This is the single most likely real failure (Jen renames a label, or the
    export shape changes underneath us) and it must be loud -- see the
    "Failure modes" section of the design doc.
    """


# ---------------------------------------------------------------------------
# Sign-in-page detection (pure text sniffing, no I/O)
# ---------------------------------------------------------------------------

# Google's sign-in page is undocumented markup too, so this is a heuristic,
# not a hard contract: absence of ANY <table> in the whole body, or one of
# these markers within the first few KB. Note the "no <table>" signal is
# doc-specific reasoning -- the target doc always has at least the
# non-card strategy/calendar tables, so a real export of *this* doc is never
# table-less. A different doc with zero tables would trip this too; that's
# an accepted false-positive for this pipeline, not a general HTML classifier.
_SIGN_IN_MARKERS = (
    "accounts.google.com",
    "servicelogin",
    "sign in - google accounts",
    "identifierid",
)
_SIGN_IN_SNIFF_WINDOW = 4096


def looks_like_sign_in_page(html: str) -> bool:
    """Return True if ``html`` looks like a Google sign-in page, not a doc export."""
    head = html[:_SIGN_IN_SNIFF_WINDOW].lower()
    has_marker = any(marker in head for marker in _SIGN_IN_MARKERS)
    has_table = "<table" in html.lower()
    return has_marker or not has_table


# ---------------------------------------------------------------------------
# Google redirector unwrapping (pure string/URL manipulation, no I/O)
# ---------------------------------------------------------------------------


def unwrap_google_redirect_url(url: str) -> str:
    """Recover the real URL from Google's ``google.com/url?q=...`` redirector.

    Every href Google Docs export emits is wrapped:
    ``https://www.google.com/url?q=<REAL_URL>&sa=D&source=editors&ust=...&usg=...``.
    The ``ust``/``usg`` values change between fetches of the identical doc, so
    callers must never hash raw HTML for change detection -- only the
    unwrapped/normalized text. If ``url`` isn't a redirector link, it is
    returned unchanged.
    """
    parsed = urlsplit(url)
    if parsed.netloc.endswith("google.com") and parsed.path == "/url":
        real = parse_qs(parsed.query).get("q")
        if real:
            return real[0]
    return url


# ---------------------------------------------------------------------------
# Status classification (pure lookup, no I/O)
# ---------------------------------------------------------------------------

# Deliberately NOT a closed enum the parser validates against -- Jen can add
# platform/status options in Google Docs at any time ("Add / Edit Options"),
# and the raw string is always carried through regardless of what this
# returns. This is classification for slice B's routing, nothing more.
_ACTIONABLE_STATUSES = frozenset({"Draft", "Ready"})
_TERMINAL_STATUSES = frozenset({"Approved", "Published"})


def classify_status(value: str | None) -> StatusClassification:
    """Classify a raw status string as actionable, terminal, or unknown.

    Slice B fires only on a transition to ``Ready``; terminal states must
    never trigger a notification. An unset chip (``None``) and any value
    outside the two known vocabularies both come back ``"unknown"`` -- never
    guess, never treat an unrecognized value as either actionable or done.
    """
    if value in _TERMINAL_STATUSES:
        return "terminal"
    if value in _ACTIONABLE_STATUSES:
        return "actionable"
    return "unknown"


# ---------------------------------------------------------------------------
# Minimal DOM-ish tree, built with stdlib html.parser
# ---------------------------------------------------------------------------


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str | None]
    children: list[_Node | str] = field(default_factory=list)


class _TreeBuilder(HTMLParser):
    """Builds a minimal tree from the export HTML.

    ``HTMLParser`` defaults to ``convert_charrefs=True``, which decodes
    entities (``&nbsp;``, ``&quot;``, ``&#39;``, ``&amp;``, ...) before they
    ever reach ``handle_data`` -- and attribute values are unescaped by the
    stdlib parser regardless of that flag. So there is no separate
    "unescape entities" pass anywhere in this module; it falls out of using
    ``html.parser`` at all.
    """

    # Tags that never wrap content in this document (none of these appear in
    # the target doc, but handling them costs nothing and avoids a parser
    # that silently mis-nests on a stray <br> inside a cell).
    _VOID_TAGS = frozenset({"br", "img", "hr", "meta", "link", "input"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node(tag="#root", attrs={})
        self._stack: list[_Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag=tag, attrs=dict(attrs))
        self._stack[-1].children.append(node)
        if tag not in self._VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack[-1].children.append(_Node(tag=tag, attrs=dict(attrs)))

    def handle_endtag(self, tag: str) -> None:
        # Lenient on purpose: Google's export has been well-formed in every
        # sample we've seen, but a stray/mismatched close tag should never
        # crash the pipeline. Pop back to the nearest matching open tag if
        # one exists on the stack; otherwise ignore the stray close.
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


def _parse_html_tree(html: str) -> _Node:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def _find_all(node: _Node, tag: str) -> list[_Node]:
    """Recursively collect every descendant element with tag name ``tag``."""
    results: list[_Node] = []
    for child in node.children:
        if isinstance(child, _Node):
            if child.tag == tag:
                results.append(child)
            results.extend(_find_all(child, tag))
    return results


def _collect_text(node: _Node) -> str:
    """Concatenate all descendant text, in document order, with no separator.

    This is the "concatenate all spans within a <p>; never rely on span
    boundaries" rule from the design doc, generalized to any node.
    """
    parts: list[str] = []
    for child in node.children:
        parts.append(child if isinstance(child, str) else _collect_text(child))
    return "".join(parts)


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_ws(text: str) -> str:
    """Collapse whitespace (including a decoded &nbsp;) to single spaces, and strip."""
    return _WHITESPACE_RE.sub(" ", text.replace("\xa0", " ")).strip()


# ---------------------------------------------------------------------------
# Card signature + field extraction
# ---------------------------------------------------------------------------

_SIGNATURE_MARKERS = ("Platform:", "Copy review")


def _is_review_card_table(table: _Node) -> bool:
    """A review card is a <table> whose text contains both signature markers.

    Tab-agnostic on purpose -- see docs/crisis-content-approval-pipeline.md
    finding 3. Never resolve a tab id, never filter by tab name; new monthly
    tabs just contribute more matching tables.
    """
    text = _collect_text(table)
    return all(marker in text for marker in _SIGNATURE_MARKERS)


_KNOWN_LABEL_PREFIXES = ("Platform:", "Asset for review", "Copy review")

# The doc's card headers always start with a month name
# ("August XX, 2026 - Welcome Back blog"). Used only as the "or looks like a
# card header" half of the unset-chip guard below.
_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _is_known_label_or_header_line(line: str) -> bool:
    return line.startswith(_KNOWN_LABEL_PREFIXES) or line.startswith(_MONTH_NAMES)


_PLATFORM_RE = re.compile(r"^Platform:\s*(.+)$")


def _next_chip_value(lines: list[str], label_index: int) -> str | None:
    """The value on the line after a label -- or None if the chip is unset.

    Mandatory guard (design doc, "Status parsing"): if the following line is
    itself a known label or looks like a card header, the chip is unset.
    Never absorb the next label's line as this label's value -- it is left
    untouched at its own index so the main loop parses it normally on its
    own turn.
    """
    next_index = label_index + 1
    if next_index >= len(lines):
        return None
    candidate = lines[next_index]
    if not candidate or _is_known_label_or_header_line(candidate):
        return None
    return candidate


def _parse_status_lines(lines: list[str]) -> tuple[str | None, str | None, str | None]:
    platform: str | None = None
    asset_status: str | None = None
    copy_status: str | None = None
    for index, line in enumerate(lines):
        platform_match = _PLATFORM_RE.match(line)
        if platform_match:
            # The platform chip is inline on this line, not on the next one.
            platform = platform_match.group(1).strip() or None
        elif line.startswith("Asset for review"):
            asset_status = _next_chip_value(lines, index)
        elif line.startswith("Copy review"):
            copy_status = _next_chip_value(lines, index)
    return platform, asset_status, copy_status


def _extract_asset_url(status_paragraphs: list[_Node]) -> str | None:
    """The asset link lives on the "Asset for review" line itself, if attached.

    "LINK" is a placeholder with no <a> until Jen attaches a real asset --
    that's normal, not an error, and must yield None rather than raising.
    """
    for paragraph in status_paragraphs:
        line = _normalize_ws(_collect_text(paragraph))
        if not line.startswith("Asset for review"):
            continue
        for anchor in _find_all(paragraph, "a"):
            href = anchor.attrs.get("href")
            if href:
                return unwrap_google_redirect_url(href)
        return None
    return None


def _split_header(header: str) -> tuple[str | None, str]:
    """Split "<date text> - <title>" on the first " - ". Falls back to (None, header)."""
    if " - " in header:
        date_text, _, title = header.partition(" - ")
        date_text = date_text.strip()
        title = title.strip()
        if date_text and title:
            return date_text, title
    return None, header


def _paragraph_lines(cell: _Node) -> list[str]:
    """Text of each direct <p> in ``cell``, normalized. Falls back to whole-cell text.

    <p> boundaries are the line structure and are load-bearing -- they are
    how chips on their own line are distinguished from the label text. A
    cell with no <p> at all (not seen in the live doc, but cheap to guard)
    falls back to treating the whole cell as one line rather than raising.
    """
    paragraphs = _find_all(cell, "p")
    if not paragraphs:
        return [_normalize_ws(_collect_text(cell))]
    return [_normalize_ws(_collect_text(p)) for p in paragraphs]


def _build_card(
    table: _Node,
    ordinal_counts: dict[tuple[str, str | None], int],
) -> ReviewCard:
    rows = _find_all(table, "tr")
    if len(rows) < 2:
        raise CrisisContentParseError(
            "Review-card table matched the signature but has fewer than 2 rows "
            "-- cannot locate header/status/copy cells."
        )
    header_cells = _find_all(rows[0], "td")
    body_cells = _find_all(rows[1], "td")
    if not header_cells or len(body_cells) < 2:
        raise CrisisContentParseError(
            "Review-card table matched the signature but is missing the header "
            "cell or the status/copy cell pair."
        )
    header_cell, status_cell, copy_cell = header_cells[0], body_cells[0], body_cells[1]

    header = _normalize_ws(_collect_text(header_cell))
    date_text, title = _split_header(header)

    status_paragraphs = _find_all(status_cell, "p")
    status_lines = (
        [_normalize_ws(_collect_text(p)) for p in status_paragraphs]
        if status_paragraphs
        else [_normalize_ws(_collect_text(status_cell))]
    )
    platform, asset_status, copy_status = _parse_status_lines(status_lines)
    asset_url = _extract_asset_url(status_paragraphs)

    copy_lines = _paragraph_lines(copy_cell)
    copy_body = "\n".join(line for line in copy_lines if line)
    copy_hash = hashlib.sha256(copy_body.encode("utf-8")).hexdigest()

    # Ordinal is a same-key counter over document order, NOT the table's
    # absolute index -- inserting a card at the top must never shift every
    # other card's identity. Two cards sharing header+platform is a live
    # possibility (see design doc); this is what disambiguates them.
    key = (header, platform)
    ordinal = ordinal_counts.get(key, 0)
    ordinal_counts[key] = ordinal + 1

    return ReviewCard(
        header=header,
        date_text=date_text,
        title=title,
        platform=platform,
        asset_status=asset_status,
        copy_status=copy_status,
        asset_url=asset_url,
        copy_body=copy_body,
        identity_key=(header, platform, ordinal),
        copy_hash=copy_hash,
    )


def parse_review_cards(html: str, *, skipped: list[str] | None = None) -> list[ReviewCard]:
    """Parse the doc's HTML export into review cards.

    Raises ``SignInPageError`` if ``html`` looks like a Google sign-in page
    rather than document content, and ``NoReviewCardsFoundError`` if the HTML
    parses but zero tables match the card signature. Both are distinct from
    a normal empty result, which this function never returns silently.
    """
    if looks_like_sign_in_page(html):
        raise SignInPageError(
            "Export response looks like a Google sign-in page (or an "
            "unexpected non-document response), not the doc export -- the "
            "access token is likely invalid or expired."
        )

    tree = _parse_html_tree(html)
    tables = _find_all(tree, "table")
    card_tables = [table for table in tables if _is_review_card_table(table)]
    if not card_tables:
        raise NoReviewCardsFoundError(
            f"Parsed {len(tables)} table(s) but none matched the review-card "
            "signature (text containing both 'Platform:' and 'Copy review'). "
            "This usually means a label was renamed or the export shape "
            "changed -- not that there is no work to do."
        )

    ordinal_counts: dict[tuple[str, str | None], int] = {}
    cards: list[ReviewCard] = []
    for index, table in enumerate(card_tables):
        try:
            cards.append(_build_card(table, ordinal_counts))
        except CrisisContentParseError as exc:
            # ONE malformed card must not take down the whole pipeline.
            #
            # Production incident 2026-08-12: Jon duplicated a card into a test
            # tab but copied only the body row, not the "August XX, 2026 - ..."
            # header row above it. That single-row table matched the signature,
            # failed _build_card, and the exception propagated out of a list
            # comprehension -- killing every poll tick. Ten perfectly good cards
            # went unprocessed and the pipeline was silently dead until someone
            # read the traceback.
            #
            # A malformed card is now skipped and reported. If EVERY card is
            # malformed the caller still gets zero cards, and
            # NoReviewCardsFoundError-style loudness still applies upstream.
            reason = f"card table #{index}: {exc}"
            logger.error("crisis_content: skipping malformed %s", reason)
            if skipped is not None:
                skipped.append(reason)
    return cards
