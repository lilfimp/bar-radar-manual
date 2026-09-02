"""Single entrypoint that dispatches a menu candidate to the right
extraction path, and applies the confidence/text scoring shared across
types. This is the only module run_extraction.py needs to import for the
"extract one menu source" step.
"""
from __future__ import annotations

from src.extraction.categorizer import classify
from src.enrichment.menu_validator import _combined_confidence
from src.extraction.html_extractor import extract_html
from src.extraction.image_extractor import extract_image
from src.extraction.pdf_extractor import extract_pdf
from src.extraction.playwright_fallback import render_and_extract
from src.utils.logging_utils import get_logger

log = get_logger(__name__)


def _keyword_confidence(text: str) -> float:
    """Reuses the same combined keyword+price-pattern scoring as Phase 1's
    validator (src/enrichment/menu_validator.py) so a menu that lists
    specific drinks/prices rather than generic category words still scores
    well here too."""
    if not text:
        return 0.0
    return _combined_confidence(text)


def extract_menu_source(candidate: dict, venue_id: str) -> dict:
    """candidate: {"url", "source_type", "link_text"} from discovery.py.
    Returns a dict ready to merge into a menu_sources row: menu_name,
    menu_category, retrieval_method, raw_file_path, extracted_text,
    extraction_status, extraction_confidence, content_hash.
    """
    url = candidate["url"]
    source_type = candidate["source_type"]
    link_text = candidate.get("link_text", "")

    if source_type == "PDF":
        result = extract_pdf(url, venue_id)
    elif source_type == "IMAGE":
        result = extract_image(url, venue_id)
    elif source_type in ("HTML_PAGE", "EXTERNAL_PLATFORM"):
        result = extract_html(url, venue_id)
        if result.pop("needs_js", False):
            log.info("HTML extraction thin for %s - escalating to Playwright", url)
            rendered = render_and_extract(url, venue_id)
            # Only replace the plain-requests result if rendering actually
            # did better; never silently downgrade to an empty result.
            if len(rendered.get("extracted_text", "")) > len(result.get("extracted_text", "")):
                result = rendered
    else:
        result = {
            "extracted_text": "",
            "extraction_status": "FAILED",
            "extraction_confidence": 0.0,
            "retrieval_method": "unknown",
            "raw_file_path": None,
            "content_hash": None,
        }

    # HTML/Playwright paths don't set a confidence themselves (unlike PDF/
    # image OCR, which have their own signal) - score by keyword content.
    if "extraction_confidence" not in result or result["retrieval_method"] in ("requests_html",):
        result["extraction_confidence"] = round(_keyword_confidence(result.get("extracted_text", "")), 2)

    result["menu_category"] = classify(url=url, link_text=link_text, text_sample=result.get("extracted_text", ""))
    result["menu_name"] = link_text or result["menu_category"].title().replace("_", " ")

    return result
