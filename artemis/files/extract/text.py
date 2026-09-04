"""Plain-text-family extraction, and the byte decoder every extractor shares.

Covers txt / md / json / yaml / log / html / xml -- anything whose value is its
characters. The interesting work here is decoding, not parsing.
"""

from __future__ import annotations

import json
from typing import Final

from artemis.files.extract.base import ExtractedFile, FileParseError, cap_text

_HTML_LIKE: Final = (".html", ".htm", ".xhtml")


def decode_bytes(payload: bytes, *, filename: str = "") -> tuple[str, str]:
    """Decode bytes to text, returning the text and a note when it was not clean.

    Order matters. UTF-8 is tried first because it is right nearly always and is
    strict enough to fail loudly when it is wrong. Only then does
    charset-normalizer guess -- and a guess is reported in the returned note, so
    an agent reading mojibake knows the encoding was inferred rather than
    concluding the data itself is garbled.

    The final fallback replaces undecodable bytes instead of raising: a mostly
    readable file with a few bad bytes is far more useful than an exception, and
    the note says plainly that characters were lost.
    """
    try:
        return payload.decode("utf-8"), ""
    except UnicodeDecodeError:
        pass

    try:
        from charset_normalizer import from_bytes

        best = from_bytes(payload).best()
        if best is not None:
            return str(
                best
            ), f"Not valid UTF-8; decoded as {best.encoding} (detected, not declared)."
    except Exception:  # detection is best-effort; never fail the read over it
        pass

    return (
        payload.decode("utf-8", errors="replace"),
        "Not valid UTF-8 and the encoding could not be detected; undecodable "
        "characters were replaced, so some text may be wrong.",
    )


def extract_text(payload: bytes, *, filename: str, mimetype: str = "text/plain") -> ExtractedFile:
    """Extract a text-family file."""
    decoded, note = decode_bytes(payload, filename=filename)
    lowered = filename.lower()
    notes = [n for n in (note,) if n]

    if lowered.endswith(".json"):
        decoded, json_note = _prettify_json(decoded, filename)
        if json_note:
            notes.append(json_note)
    elif lowered.endswith(_HTML_LIKE):
        decoded = _strip_html(decoded)
        notes.append("HTML tags stripped; only the visible text is shown.")

    if not decoded.strip():
        raise FileParseError(f"{filename} decoded successfully but contains no text.")

    text, truncated = cap_text(decoded)
    return ExtractedFile(
        filename=filename,
        mimetype=mimetype,
        kind="text",
        text=text,
        size_bytes=len(payload),
        truncated=truncated,
        notes=notes,
    )


def _prettify_json(raw: str, filename: str) -> tuple[str, str]:
    """Re-indent JSON for readability, leaving it untouched when it does not parse.

    Invalid JSON is returned AS-IS with a note rather than raising: a malformed
    config file is usually exactly what the person wants looked at, and refusing
    to show it would hide the very problem.
    """
    try:
        return json.dumps(json.loads(raw), indent=2, ensure_ascii=False), ""
    except (ValueError, RecursionError):
        return raw, f"{filename} is not valid JSON; showing the raw text instead."


def _strip_html(raw: str) -> str:
    """Reduce HTML to its visible text, preferring the main content region.

    Two passes. The first collects only what sits inside ``<main>``,
    ``<article>`` or ``role="main"``; the second collects the whole body. The
    main-region text wins when there is a meaningful amount of it.

    Without that preference a government page is unusable: michigan.gov's
    literacy-law page is 334 KB, and its first several thousand characters are
    the site's navigation menu. An agent asked for the statute's grade bands
    would read a list of every MDE programme area and never reach the law
    (measured 2026-08-31).

    Uses the stdlib parser rather than a regex so that script and style bodies
    are dropped by STRUCTURE — a regex that strips tags happily leaves a page's
    entire JavaScript payload behind as "text".
    """
    from html.parser import HTMLParser

    # Chrome that is visible text but never the answer.
    skip_tags = {"script", "style", "nav", "header", "footer", "aside", "noscript", "svg"}
    main_tags = {"main", "article"}

    class _Stripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.all_chunks: list[str] = []
            self.main_chunks: list[str] = []
            self._skip = 0
            self._main = 0
            self._stack: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            self._stack.append(tag)
            if tag in skip_tags:
                self._skip += 1
            if tag in main_tags or dict(attrs).get("role") == "main":
                self._main += 1

        def handle_endtag(self, tag: str) -> None:
            if tag in skip_tags and self._skip:
                self._skip -= 1
            # Only close a main region opened by one of these tag names.
            if tag in main_tags and self._main:
                self._main -= 1
            if tag in self._stack:
                while self._stack and self._stack.pop() != tag:
                    pass

        def handle_data(self, data: str) -> None:
            if self._skip:
                return
            text = data.strip()
            if not text:
                return
            self.all_chunks.append(text)
            if self._main:
                self.main_chunks.append(text)

    parser = _Stripper()
    try:
        parser.feed(raw)
    except Exception:
        return raw

    main_text = "\n".join(parser.main_chunks)
    if len(main_text) >= 200:
        return main_text
    return "\n".join(parser.all_chunks)
