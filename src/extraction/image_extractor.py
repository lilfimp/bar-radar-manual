"""Extract text from a menu that IS an image (e.g. a photographed chalkboard
menu, or a JPG/PNG scan linked directly)."""
from __future__ import annotations

from src.extraction.evidence_store import content_hash, save_evidence
from src.extraction.ocr_utils import ocr_image_bytes
from src.utils.http_utils import get
from src.utils.logging_utils import get_logger

log = get_logger(__name__)


def extract_image(url: str, venue_id: str) -> dict:
    resp = get(url)
    if resp is None:
        return _fail("FAILED", "no response")
    if resp.status_code in (403, 429):
        return _fail("BLOCKED", f"http {resp.status_code}")
    if resp.status_code >= 400:
        return _fail("FAILED", f"http {resp.status_code}")

    raw_bytes = resp.content
    ext = "." + url.split(".")[-1].split("?")[0].lower() if "." in url else ".jpg"
    raw_path = save_evidence(venue_id, raw_bytes, kind="image", ext=ext)

    text, confidence = ocr_image_bytes(raw_bytes)
    status = "SCREENSHOT_OCR" if text.strip() else "MANUAL_REVIEW"

    return {
        "extracted_text": text,
        "extraction_status": status,
        "extraction_confidence": round(confidence, 2),
        "retrieval_method": "image_ocr",
        "raw_file_path": raw_path,
        "content_hash": content_hash(raw_bytes),
    }


def _fail(status: str, reason: str) -> dict:
    log.info("Image extraction failed: %s", reason)
    return {
        "extracted_text": "",
        "extraction_status": status,
        "extraction_confidence": 0.0,
        "retrieval_method": "image_ocr",
        "raw_file_path": None,
        "content_hash": None,
    }
