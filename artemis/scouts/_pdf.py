"""Shared PDF text extraction for all Artemis scout workers.

Primary extraction: pypdfium2 (fast, accurate on digitally-created PDFs).
OCR fallback: pytesseract (for image-only / scanned PDFs).

If tesseract is not installed, OCR falls back gracefully — a warning is
logged and an empty string is returned for image-only pages.

Usage::

    text = extract_text(pdf_bytes)

    # Limit to first 20 + last 5 pages (board minutes pattern)
    text = extract_text(pdf_bytes, first_pages=20, last_pages=5)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

# Minimum characters on a page to consider it text-bearing (not image-only).
_TEXT_PAGE_THRESHOLD = 30

# ---------------------------------------------------------------------------
# OCR availability probe — lazy, cached at import time
# ---------------------------------------------------------------------------

_TESSERACT_AVAILABLE: bool | None = None


def _has_tesseract() -> bool:
    global _TESSERACT_AVAILABLE
    if _TESSERACT_AVAILABLE is None:
        try:
            import pytesseract  # type: ignore[import-not-found]  # noqa: F401

            pytesseract.get_tesseract_version()  # raises if binary missing
            _TESSERACT_AVAILABLE = True
        except Exception:
            _TESSERACT_AVAILABLE = False
    return _TESSERACT_AVAILABLE


# ---------------------------------------------------------------------------
# Page-level text extraction
# ---------------------------------------------------------------------------


def _extract_page_text(page: Any) -> str:
    """Return text from a pypdfium2 page object."""
    textpage: Any = page.get_textpage()
    try:
        return str(textpage.get_text_range() or "")
    finally:
        textpage.close()


def _ocr_page(page: Any) -> str:
    """Render a pypdfium2 page to an image and run OCR."""
    if not _has_tesseract():
        _logger.warning("Tesseract not installed — OCR unavailable for image-only PDF page.")
        return ""

    try:
        import pytesseract  # noqa: PLC0415

        bitmap: Any = page.render(scale=2.0)
        pil_image: Any = bitmap.to_pil()
        bitmap.close()
        text: str = pytesseract.image_to_string(pil_image)
        return text
    except Exception as exc:
        _logger.warning("OCR failed for page: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_text(
    source: bytes | str | Path,
    *,
    first_pages: int | None = None,
    last_pages: int | None = None,
    _open_fn: Callable[[bytes | str | Path], object] | None = None,
) -> str:
    """Extract text from a PDF.

    Parameters
    ----------
    source:
        PDF bytes, a file path, or a path string.
    first_pages:
        If set, only extract text from the first *N* pages.
    last_pages:
        If set, also extract text from the last *M* pages.  Combined with
        *first_pages*, pages are deduplicated so the same page isn't
        included twice.
    _open_fn:
        Inject a PDF-open function — **tests only**.  Receives *source* and
        should return an object with a ``__len__`` (page count) and
        ``__getitem__`` (page access by index).

    Returns
    -------
    str
        Concatenated text, pages separated by ``\\n\\n``.
    """
    import pypdfium2  # type: ignore[import-untyped]

    if _open_fn is not None:
        doc = _open_fn(source)
    elif isinstance(source, bytes):
        doc = pypdfium2.PdfDocument(source)
    else:
        doc = pypdfium2.PdfDocument(str(source))

    try:
        page_count: int = len(doc)  # type: ignore[arg-type]
        if page_count == 0:
            return ""

        # Build ordered, deduplicated page index list.
        indices: list[int] = []
        seen: set[int] = set()

        first_n = first_pages if first_pages is not None else page_count
        for i in range(min(first_n, page_count)):
            if i not in seen:
                indices.append(i)
                seen.add(i)

        if last_pages is not None:
            start = max(0, page_count - last_pages)
            for i in range(start, page_count):
                if i not in seen:
                    indices.append(i)
                    seen.add(i)

        parts: list[str] = []
        for i in indices:
            page = doc[i]  # type: ignore[index]
            try:
                text = _extract_page_text(page)
                if len(text.strip()) < _TEXT_PAGE_THRESHOLD:
                    # Likely image-only page — attempt OCR.
                    text = _ocr_page(page)
                if text.strip():
                    parts.append(text.strip())
            finally:
                page.close()

        return "\n\n".join(parts)
    finally:
        doc.close()  # type: ignore[attr-defined]
