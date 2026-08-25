"""Shared types for file extraction.

The central type is `ExtractedFile`: what an agent gets to read. It is
deliberately NOT the file -- it is a bounded, already-truncated rendering, so a
caller cannot accidentally push a 200k-row export into a prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Ceiling on bytes we will pull from a source. Above this we refuse and SAY we
# refused; a stray multi-hundred-MB upload must not be able to wedge the app.
MAX_DOWNLOAD_BYTES: int = 25 * 1024 * 1024

# Ceiling on extracted text we retain. Chosen to be generous for documents while
# staying far below any model's context: the point of extraction is to give an
# agent something readable, not to relay the file.
MAX_EXTRACTED_CHARS: int = 200_000

# Tabular files are summarised by SHAPE, not dumped. A spreadsheet's value to an
# agent is its columns and a feel for the rows; the full grid is noise it cannot
# hold anyway. The full row count is always reported even when rows are elided,
# so the agent never mistakes a sample for the whole.
MAX_TABULAR_SAMPLE_ROWS: int = 50


class ExtractionError(Exception):
    """Base for every failure in this package.

    Carries a `reason` written to be repeated verbatim to a human. Per the
    lesson in CLAUDE.md, a tool must never report success for work it did not
    do -- and a failure that cannot distinguish "not permitted" from "could not
    parse" sends people hunting the wrong problem.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class UnsupportedFileTypeError(ExtractionError):
    """The file arrived intact but nothing here knows how to read it."""


class FileTooLargeError(ExtractionError):
    """The file exceeds `MAX_DOWNLOAD_BYTES` and was deliberately not fetched."""


class FileParseError(ExtractionError):
    """A supported type that could not be parsed (corrupt, encrypted, malformed)."""


class AccessDeniedError(ExtractionError):
    """The source refused to hand over the file.

    Kept distinct from every parse failure above because the remedy is entirely
    different -- someone has to share the file or grant a scope -- and conflating
    the two is precisely what sent people hunting an allowlist for what was a
    data problem (CLAUDE.md, 2026-08-12).
    """


@dataclass(frozen=True)
class TabularShape:
    """The summary of a spreadsheet-like file: columns, size, a sample."""

    columns: list[str]
    total_rows: int
    sample_rows: list[list[str]]
    sheet_name: str | None = None
    truncated: bool = False


@dataclass(frozen=True)
class ExtractedFile:
    """A bounded, readable rendering of one file.

    `text` is what an agent reads. `tables` carries structure when the source was
    tabular, so a caller that wants columns does not have to re-parse prose.
    """

    filename: str
    mimetype: str
    kind: Literal["tabular", "document", "text", "image", "unknown"]
    text: str
    size_bytes: int
    source: str = ""
    source_url: str = ""
    tables: list[TabularShape] = field(default_factory=list)
    truncated: bool = False
    notes: list[str] = field(default_factory=list)

    def summary_line(self) -> str:
        """One line an agent can use to acknowledge the file it received.

        Genuinely one line: a real workbook can carry 20+ tabs (measured against
        "Amira Teacher Resources-INTERNAL" on 2026-08-25), and naming every one
        turns an acknowledgement into a wall of text. Beyond three tabs it
        reports the count and lets `tables` carry the detail for anyone who
        needs it.
        """
        bits = [f"{self.filename} ({self.mimetype}, {self.size_bytes:,} bytes)"]
        if len(self.tables) > 3:
            total = sum(t.total_rows for t in self.tables)
            bits.append(f"{len(self.tables)} tabs, {total:,} rows total")
        else:
            for table in self.tables:
                label = f" [{table.sheet_name}]" if table.sheet_name else ""
                bits.append(f"{table.total_rows:,} rows x {len(table.columns)} cols{label}")
        if self.truncated:
            bits.append("truncated")
        return " - ".join(bits)


def cap_text(value: str, limit: int = MAX_EXTRACTED_CHARS) -> tuple[str, bool]:
    """Trim to `limit`, reporting whether anything was cut.

    Returns the flag rather than silently truncating: a caller that does not know
    text was cut will describe a partial file as if it were the whole one.
    """
    if len(value) <= limit:
        return value, False
    return value[:limit], True
