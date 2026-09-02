"""Extract text from a PDF menu.

Two-stage approach:
1. Try direct text extraction with pdfplumber - fast, accurate, works for
   any PDF that has a real text layer (the large majority of modern menu
   PDFs exported from Word/Canva/etc).
2. If that yields near-nothing (a strong signal it's a scanned/photographed
   PDF with no text layer), render each page to an image with PyMuPDF and
   OCR each page with Tesseract.

Only the first few pages are processed (menus are rarely more than a few
pages; this caps worst-case runtime on an oddly large PDF).
"""
from __future__ import annotations

import io

from src.extraction.evidence_store import content_hash, save_evidence
from src.extraction.ocr_utils import ocr_image_bytes
from src.utils.http_utils import get
from src.utils.logging_utils import get_logger

log = get_logger(__name__)

MAX_PAGES = 8
MIN_USABLE_TEXT_CHARS = 80


def _extract_text_pdfplumber(pdf_bytes: bytes) -> str:
    try:
        import pdfplumber
    except ImportError:
        log.warning("pdfplumber not installed - skipping direct PDF text extraction")
        return ""
    try:
        text_parts = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:MAX_PAGES]:
                page_text = page.extract_text() or ""
                text_parts.append(page_text)
        return "\n".join(text_parts).strip()
    except Exception as exc:  # noqa: BLE001 - malformed PDFs are common in the wild
        log.warning("pdfplumber failed: %s", exc)
        return ""


def _ocr_pdf_pages(pdf_bytes: bytes) -> tuple[str, float]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        log.warning("PyMuPDF not installed - cannot render PDF pages for OCR")
        return "", 0.0

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        log.warning("PyMuPDF failed to open PDF: %s", exc)
        return "", 0.0

    texts = []
    confidences = []
    for page_index in range(min(len(doc), MAX_PAGES)):
        page = doc[page_index]
        # 2x zoom for better OCR accuracy on typically-small menu fonts
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        image_bytes = pix.tobytes("png")
        text, conf = ocr_image_bytes(image_bytes)
        if text.strip():
            texts.append(text)
            confidences.append(conf)
    doc.close()

    combined = "\n".join(texts).strip()
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return combined, avg_conf


def extract_pdf(url: str, venue_id: str) -> dict:
    resp = get(url)
    if resp is None:
        return _fail("FAILED", "no response")
    if resp.status_code in (403, 429):
        return _fail("BLOCKED", f"http {resp.status_code}")
    if resp.status_code >= 400:
        return _fail("FAILED", f"http {resp.status_code}")

    raw_bytes = resp.content
    raw_path = save_evidence(venue_id, raw_bytes, kind="pdf")

    text = _extract_text_pdfplumber(raw_bytes)
    if len(text) >= MIN_USABLE_TEXT_CHARS:
        return {
            "extracted_text": text,
            "extraction_status": "EXTRACTED",
            "extraction_confidence": 0.9,  # direct text layer - high trust
            "retrieval_method": "pdf_text",
            "raw_file_path": raw_path,
            "content_hash": content_hash(raw_bytes),
        }

    # No usable text layer - fall back to OCR on rendered pages.
    ocr_text, ocr_confidence = _ocr_pdf_pages(raw_bytes)
    if ocr_text.strip():
        return {
            "extracted_text": ocr_text,
            "extraction_status": "PDF_OCR",
            "extraction_confidence": round(ocr_confidence, 2),
            "retrieval_method": "pdf_ocr",
            "raw_file_path": raw_path,
            "content_hash": content_hash(raw_bytes),
        }

    return {
        "extracted_text": text,  # keep whatever scraps pdfplumber found, if any
        "extraction_status": "MANUAL_REVIEW",
        "extraction_confidence": 0.1,
        "retrieval_method": "pdf_text",
        "raw_file_path": raw_path,
        "content_hash": content_hash(raw_bytes),
    }


def _fail(status: str, reason: str) -> dict:
    log.info("PDF extraction failed: %s", reason)
    return {
        "extracted_text": "",
        "extraction_status": status,
        "extraction_confidence": 0.0,
        "retrieval_method": "pdf_text",
        "raw_file_path": None,
        "content_hash": None,
    }
