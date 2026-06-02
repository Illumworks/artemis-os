# Service — PDF Extractor

Extracts text from PDFs. Used by 1.2 Regional News Scout, 1.6 State DoE Scout, 1.7 Procurement Scout, and 1.8 Board Minutes Scout.

**Module path:** `artemis_os/services/pdf_extractor.py`

## Interface

```python
from typing import Optional
from dataclasses import dataclass

@dataclass
class ExtractedPDF:
    text: str
    pages: int
    extraction_method: str  # "native" | "ocr"
    quality_score: float    # 0–1; affects source_quality_low flag
    page_count_text_layer: int
    page_count_ocr_fallback: int

def extract(url: str, timeout_seconds: int = 60) -> ExtractedPDF:
    """
    Download PDF from URL, extract text. Two-stage approach:

    1. Try pypdfium2 for native text extraction.
    2. For pages where native extraction yields <50 characters, fall back to OCR (pytesseract).

    Quality score:
      1.0  if all pages extracted natively
      0.5  if some pages required OCR
      0.2  if all pages required OCR (scanned image, lowest confidence)

    Raises PDFExtractionError if PDF unreachable or corrupt.
    """

def extract_from_bytes(pdf_bytes: bytes) -> ExtractedPDF:
    """Same as extract() but takes raw bytes. Used when calling code already has the PDF."""
```

## Quality score handling for scouts

Scouts MUST check `quality_score` after extraction:

```python
extracted = pdf_extractor.extract(url)
if extracted.quality_score < 0.5:
    signal.flags.append("source_quality_low")
```

This flag propagates through to the Signals Inbox so humans can see the evidence may be unreliable.

## Implementation notes

- Cache extracted PDFs by URL hash for 7 days. Many sources serve the same PDF repeatedly (board meeting agendas, recurring RFP postings).
- Use a process pool for OCR — pytesseract is CPU-bound and blocks the event loop.
- For large PDFs (>50 pages), extract first 20 + last 5 pages only. Board minutes often have long appendices that aren't relevant.

## Dependencies

```
pypdfium2 >= 4.30
pytesseract >= 0.3.10
Pillow >= 10.0
httpx >= 0.27
```

System dependency: `tesseract-ocr` (install via apt / brew).

## Failure modes

- URL returns 404 → raise `PDFNotFoundError`, scout logs and skips.
- URL returns 200 but body isn't PDF → raise `PDFInvalidError`, scout logs and skips.
- PDF is password-protected → raise `PDFLockedError`, scout flags to `unresolved_signals` for manual review.
- Tesseract not installed → raise `OCRUnavailableError` at startup, not at call time. Scout falls back to native-only.
