"""PDF text acquisition for resume extraction (paper Figure 8).

Reads the PDF text layer with PyMuPDF; when a page carries no usable text layer
(a scanned/image-only PDF) it falls back to OCR by rasterizing the page and
running Tesseract. Pure I/O over bytes — no domain-model coupling — so it stays
unit-testable and reusable.

Kept out of ``app.matching.__init__`` re-exports on purpose: importing it pulls
in PyMuPDF/Pillow, so callers import it explicitly
(``from app.matching.extraction import extract_text``).
"""

import io
from dataclasses import dataclass

import fitz
import pytesseract
from PIL import Image

# Cap on pages processed — resumes are short, and OCR is slow; anything past this
# is ignored to bound latency.
MAX_PAGES = 8
OCR_DPI = 200
# Below this many characters, a page's text layer is treated as empty (a scanned
# image), triggering the per-page OCR fallback.
_SPARSE_CHARS_PER_PAGE = 40

# A PyMuPDF text block: (x0, y0, x1, y1, text, block_no, block_type). block_type
# 0 is a text block, 1 an image block.
_Block = tuple[float, float, float, float, str, int, int]


@dataclass(frozen=True)
class ExtractMeta:
    """Provenance of the extracted text, surfaced to the UI for transparency."""

    method: str  # "text" (PDF text layer) or "ocr" (rasterized + Tesseract)
    ocr_used: bool
    pages: int
    char_count: int


class ExtractionError(Exception):
    """The PDF could not be opened or read (corrupt or password-protected)."""


def extract_text(pdf_bytes: bytes) -> tuple[str, ExtractMeta]:
    """Extract text from a PDF, using OCR only for pages without a text layer.

    Raises ``ExtractionError`` for corrupt or encrypted PDFs.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises fitz.FileDataError and friends
        raise ExtractionError("Could not open the PDF file") from exc

    try:
        if doc.needs_pass:
            raise ExtractionError("The PDF is password-protected")
        page_count = doc.page_count
        texts: list[str] = []
        ocr_used = False
        for index in range(min(page_count, MAX_PAGES)):
            page = doc.load_page(index)
            page_text = "\n".join(_blocks_in_reading_order(page)).strip()
            if len(page_text) < _SPARSE_CHARS_PER_PAGE:
                ocr_text = _ocr_page(page).strip()
                if ocr_text:
                    ocr_used = True
                    page_text = ocr_text
            if page_text:
                texts.append(page_text)
    finally:
        doc.close()

    raw_text = "\n".join(texts).strip()
    meta = ExtractMeta(
        method="ocr" if ocr_used else "text",
        ocr_used=ocr_used,
        pages=page_count,
        char_count=len(raw_text),
    )
    return raw_text, meta


def _ocr_page(page: fitz.Page) -> str:
    pixmap = page.get_pixmap(dpi=OCR_DPI)
    image = Image.open(io.BytesIO(pixmap.tobytes("png")))
    try:
        return str(pytesseract.image_to_string(image))
    except pytesseract.TesseractNotFoundError:
        # Tesseract binary not installed — degrade to no OCR text (the caller
        # returns empty fields) rather than raising a 500.
        return ""


def _blocks_in_reading_order(page: fitz.Page) -> list[str]:
    """Return a page's text blocks in human reading order.

    Plain ``page.get_text()`` interleaves side-by-side columns line by line, which
    scrambles two-column resumes (skills/education end up spliced into work
    history). This reconstructs order from block geometry: a genuine two-column
    layout is read left column top-to-bottom, then right; anything else falls back
    to a stable top-to-bottom, left-to-right sort.
    """
    blocks: list[_Block] = [
        block
        for block in page.get_text("blocks")
        if block[6] == 0 and isinstance(block[4], str) and block[4].strip()
    ]
    if len(blocks) < 2:
        return [block[4].strip() for block in blocks]

    width = float(page.rect.width) or 1.0
    mid = width / 2
    left = [block for block in blocks if block[0] < mid]
    right = [block for block in blocks if block[0] >= mid]

    if _is_two_column(left, right, width):
        ordered = sorted(left, key=_top_left) + sorted(right, key=_top_left)
    else:
        ordered = sorted(blocks, key=_top_left)
    return [block[4].strip() for block in ordered]


def _top_left(block: _Block) -> tuple[float, float]:
    return (block[1], block[0])


def _is_two_column(left: list[_Block], right: list[_Block], width: float) -> bool:
    """Detect a real two-column layout vs. incidental right-aligned content.

    Requires a right-hand stack of blocks whose left neighbours stay in the left
    half (narrow) — so right-aligned dates on wide single-column lines, where the
    left block spans past the midline, are *not* misread as a second column.
    """
    if len(left) < 2 or len(right) < 2:
        return False
    narrow_limit = width / 2 + 0.05 * width
    narrow = sum(1 for block in left if block[2] <= narrow_limit)
    if narrow < 0.6 * len(left):
        return False
    left_lo, left_hi = min(b[1] for b in left), max(b[3] for b in left)
    right_lo, right_hi = min(b[1] for b in right), max(b[3] for b in right)
    overlap = min(left_hi, right_hi) - max(left_lo, right_lo)
    return overlap > 0.3 * min(left_hi - left_lo, right_hi - right_lo)
