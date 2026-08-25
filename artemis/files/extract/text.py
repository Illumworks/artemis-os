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
    """Reduce HTML to its visible text.

    Uses the stdlib parser rather than a regex so that script and style bodies
    are dropped by structure -- a regex that strips tags happily leaves a page's
    entire JavaScript payload behind as "text".
    """
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.chunks: list[str] = []
            self._skip = 0

        def handle_starttag(self, tag: str, attrs: object) -> None:
            if tag in {"script", "style"}:
                self._skip += 1

        def handle_endtag(self, tag: str) -> None:
            if tag in {"script", "style"} and self._skip:
                self._skip -= 1

        def handle_data(self, data: str) -> None:
            if not self._skip and data.strip():
                self.chunks.append(data.strip())

    parser = _Stripper()
    try:
        parser.feed(raw)
    except Exception:
        return raw
    return "\n".join(parser.chunks)
