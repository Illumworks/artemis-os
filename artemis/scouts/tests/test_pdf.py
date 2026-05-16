"""Tests for artemis.scouts._pdf — PDF text extraction with OCR fallback."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

from artemis.scouts._pdf import extract_text

# ---------------------------------------------------------------------------
# Fake pypdfium2 page/document helpers
# ---------------------------------------------------------------------------


_LONG = "x" * 50  # ensures text exceeds _TEXT_PAGE_THRESHOLD so OCR is not triggered


def _fake_page(text: str, *, close: bool = True) -> MagicMock:
    """Build a mock pypdfium2 page that returns *text* (must exceed threshold)."""
    textpage = MagicMock()
    textpage.get_text_range.return_value = text
    textpage.close = MagicMock()

    page = MagicMock()
    page.get_textpage.return_value = textpage
    page.close = MagicMock()
    return page


def _fake_doc(pages: list[MagicMock]) -> MagicMock:
    doc = MagicMock()
    doc.__len__ = MagicMock(return_value=len(pages))
    doc.__getitem__ = MagicMock(side_effect=lambda i: pages[i])
    doc.close = MagicMock()
    return doc


def _open_fn(pages: list[MagicMock]) -> Callable[[Any], Any]:
    """Return an _open_fn that produces a fake doc with the given pages."""
    doc = _fake_doc(pages)

    def _fn(source: Any) -> Any:
        return doc

    return _fn


# ---------------------------------------------------------------------------
# Basic extraction
# ---------------------------------------------------------------------------


def test_extract_text_from_text_pdf() -> None:
    p1 = "Hello world " + _LONG
    p2 = "Second page " + _LONG
    pages = [_fake_page(p1), _fake_page(p2)]
    text = extract_text(b"fake_pdf", _open_fn=_open_fn(pages))
    assert "Hello world" in text
    assert "Second page" in text


def test_extract_text_empty_pdf_returns_empty_string() -> None:
    doc = MagicMock()
    doc.__len__ = MagicMock(return_value=0)
    doc.close = MagicMock()

    def _fn(source: Any) -> Any:
        return doc

    result = extract_text(b"", _open_fn=_fn)
    assert result == ""


def test_extract_text_pages_separated_by_double_newline() -> None:
    pages = [_fake_page("Page one text " + _LONG), _fake_page("Page two text " + _LONG)]
    text = extract_text(b"pdf", _open_fn=_open_fn(pages))
    assert "\n\n" in text


# ---------------------------------------------------------------------------
# first_pages / last_pages slicing
# ---------------------------------------------------------------------------


def _label(n: int) -> str:
    return f"Page {n} " + _LONG  # long enough to pass threshold


def test_first_pages_limits_extraction() -> None:
    pages = [_fake_page(_label(i)) for i in range(5)]
    text = extract_text(b"pdf", first_pages=2, _open_fn=_open_fn(pages))
    assert "Page 0" in text
    assert "Page 1" in text
    assert "Page 2" not in text


def test_last_pages_appends_tail() -> None:
    pages = [_fake_page(_label(i)) for i in range(5)]
    text = extract_text(b"pdf", first_pages=2, last_pages=2, _open_fn=_open_fn(pages))
    assert "Page 0" in text
    assert "Page 1" in text
    assert "Page 3" in text
    assert "Page 4" in text


def test_last_pages_no_duplicate_pages() -> None:
    """When first_pages + last_pages overlap, pages aren't duplicated."""
    pages = [_fake_page(_label(i)) for i in range(3)]
    # first_pages=3 covers all; last_pages=2 overlaps
    text = extract_text(b"pdf", first_pages=3, last_pages=2, _open_fn=_open_fn(pages))
    # "Page 1" should appear exactly once
    assert text.count("Page 1") == 1


# ---------------------------------------------------------------------------
# OCR fallback
# ---------------------------------------------------------------------------


def test_image_only_page_triggers_ocr_when_tesseract_available() -> None:
    """A page with sparse text triggers OCR; OCR result is used."""
    short_text = "x"  # below threshold
    page = _fake_page(short_text)

    with (
        patch("artemis.scouts._pdf._has_tesseract", return_value=True),
        patch("artemis.scouts._pdf._ocr_page", return_value="OCR extracted text") as mock_ocr,
    ):
        text = extract_text(b"pdf", _open_fn=_open_fn([page]))

    mock_ocr.assert_called_once()
    assert "OCR extracted text" in text


def test_image_only_page_returns_empty_when_tesseract_unavailable() -> None:
    """When tesseract is missing, image-only pages contribute empty string."""
    short_text = "x"
    page = _fake_page(short_text)

    with patch("artemis.scouts._pdf._has_tesseract", return_value=False):
        text = extract_text(b"pdf", _open_fn=_open_fn([page]))

    # Image-only page with no tesseract → empty contribution
    assert text == ""
