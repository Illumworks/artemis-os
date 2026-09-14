"""Scanned board documents must not read as dead links.

District board packets are routinely scanned images rather than digital text.
Before tesseract was installed on this machine, `extract_text` returned an error
for those pages and the scout reported the document unreadable — indistinguishable
from a broken URL. Of twelve Dallas ISD attachments checked on 2026-09-14, one had
no extractable text at all; with OCR available all twelve extract.
"""

from __future__ import annotations

import shutil

import pytest

from artemis.scouts._pdf import _has_tesseract, _ocr_page

_TESSERACT_ON_PATH = shutil.which("tesseract") is not None


def test_the_ocr_probe_agrees_with_the_binary_on_disk() -> None:
    """`_has_tesseract` is cached, so a wrong answer is wrong for the life of the
    process. It must track reality, not a stale import-time guess."""
    assert _has_tesseract() == _TESSERACT_ON_PATH


@pytest.mark.skipif(not _TESSERACT_ON_PATH, reason="tesseract not installed here")
def test_ocr_is_available_in_this_environment() -> None:
    """A guard for the deployment, not for the code: if this fails on the Mac
    mini, scanned board documents are silently unreadable again. `brew install
    tesseract` — see the quickstart in CLAUDE.md."""
    import pytesseract

    assert pytesseract.get_tesseract_version()


def test_a_page_that_cannot_be_rendered_degrades_rather_than_raising() -> None:
    """OCR sits in the fallback path for a page that yielded no text. It must
    never turn an unreadable page into a failed run — the other pages of that
    document, and the other documents, are still worth having."""

    class _Unrenderable:
        def render(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("no raster for you")

    assert _ocr_page(_Unrenderable()) == ""
