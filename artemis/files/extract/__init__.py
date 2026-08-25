"""Extraction dispatch: bytes + a filename in, an `ExtractedFile` out.

Routing prefers the FILENAME EXTENSION over the declared mimetype. Slack's
`mimetype` on an upload is frequently `text/plain` for a .tsv and
`application/octet-stream` for anything it does not recognise, so trusting it
sends real spreadsheets down the plain-text path. The extension is what the
person who made the file chose; the mimetype is what an intermediary guessed.
Mimetype is still consulted as a fallback when the name carries no extension.
"""

from __future__ import annotations

from collections.abc import Callable

from artemis.files.extract.base import (
    MAX_DOWNLOAD_BYTES,
    ExtractedFile,
    FileTooLargeError,
    UnsupportedFileTypeError,
)
from artemis.files.extract.documents import describe_image, extract_docx, extract_pdf
from artemis.files.extract.tabular import extract_delimited, extract_xlsx
from artemis.files.extract.text import extract_text

Extractor = Callable[..., ExtractedFile]

_BY_EXTENSION: dict[str, Extractor] = {
    # tabular
    ".csv": extract_delimited,
    ".tsv": extract_delimited,
    ".tab": extract_delimited,
    ".xlsx": extract_xlsx,
    ".xlsm": extract_xlsx,
    # documents
    ".pdf": extract_pdf,
    ".docx": extract_docx,
    # text family
    ".txt": extract_text,
    ".md": extract_text,
    ".markdown": extract_text,
    ".json": extract_text,
    ".yaml": extract_text,
    ".yml": extract_text,
    ".log": extract_text,
    ".html": extract_text,
    ".htm": extract_text,
    ".xml": extract_text,
    ".sql": extract_text,
    ".py": extract_text,
    ".js": extract_text,
    ".ts": extract_text,
    ".sh": extract_text,
    ".ini": extract_text,
    ".cfg": extract_text,
    ".toml": extract_text,
    # images — acknowledged, not read
    ".png": describe_image,
    ".jpg": describe_image,
    ".jpeg": describe_image,
    ".gif": describe_image,
    ".webp": describe_image,
    ".heic": describe_image,
    ".svg": describe_image,
}

_BY_MIMETYPE: dict[str, Extractor] = {
    "text/csv": extract_delimited,
    "text/tab-separated-values": extract_delimited,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": extract_xlsx,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": extract_docx,
    "application/pdf": extract_pdf,
    "application/json": extract_text,
    "text/plain": extract_text,
    "text/markdown": extract_text,
    "text/html": extract_text,
}

# Formats we recognise well enough to name in a refusal. Saying "I can see a
# .doc and cannot read the legacy Word format" is actionable; "unsupported file"
# is not, and leaves the sender guessing whether the upload even arrived.
_KNOWN_UNSUPPORTED: dict[str, str] = {
    ".doc": "the legacy Word .doc format (re-save it as .docx)",
    ".xls": "the legacy Excel .xls format (re-save it as .xlsx)",
    ".ppt": "the legacy PowerPoint .ppt format",
    ".pptx": "PowerPoint decks",
    ".zip": "archives (unzip and share the file itself)",
    ".rar": "archives (unpack and share the file itself)",
    ".7z": "archives (unpack and share the file itself)",
    ".mp4": "video files",
    ".mov": "video files",
    ".mp3": "audio files",
    ".wav": "audio files",
    ".key": "Keynote decks",
    ".numbers": "Numbers spreadsheets (export to .xlsx or .csv)",
    ".pages": "Pages documents (export to .docx or .pdf)",
}


def _extension_of(filename: str) -> str:
    _, _, tail = filename.lower().rpartition(".")
    return f".{tail}" if tail and tail != filename.lower() else ""


def supported_extensions() -> frozenset[str]:
    """Every extension this layer can read. Used by tests and by the tool schema."""
    return frozenset(_BY_EXTENSION)


def extract(
    payload: bytes,
    *,
    filename: str,
    mimetype: str = "",
    source: str = "",
    source_url: str = "",
) -> ExtractedFile:
    """Route `payload` to the right extractor and return a bounded rendering.

    Raises `UnsupportedFileTypeError` naming the format when nothing can read it,
    and `FileTooLargeError` when the payload exceeds the ceiling. Both carry a
    `reason` written to be repeated to the person who shared the file.
    """
    if len(payload) > MAX_DOWNLOAD_BYTES:
        raise FileTooLargeError(
            f"{filename} is {len(payload):,} bytes, over the "
            f"{MAX_DOWNLOAD_BYTES:,}-byte limit, so it was not read."
        )

    extension = _extension_of(filename)

    if extension in _KNOWN_UNSUPPORTED:
        raise UnsupportedFileTypeError(
            f"{filename} is {_KNOWN_UNSUPPORTED[extension]}, which cannot be read yet."
        )

    extractor = _BY_EXTENSION.get(extension) or _BY_MIMETYPE.get(mimetype.split(";")[0].strip())
    if extractor is None:
        raise UnsupportedFileTypeError(
            f"{filename} has an unrecognised format"
            + (f" ({extension})" if extension else "")
            + f"{f' / {mimetype}' if mimetype else ''} and cannot be read."
        )

    result = extractor(payload, filename=filename, mimetype=mimetype)
    if source or source_url:
        from dataclasses import replace

        result = replace(result, source=source, source_url=source_url)
    return result


__all__ = ["extract", "supported_extensions"]
