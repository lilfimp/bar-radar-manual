"""OCR via Tesseract (pytesseract). Local, free, no API key.

Chosen over PaddleOCR for this project deliberately: Tesseract installs in
one `apt-get install tesseract-ocr` line on GitHub's Ubuntu runners with no
extra ML-stack weight, which matters a lot for keeping CI runs fast and
free. PaddleOCR is generally more accurate on messy layouts, so if OCR
quality on real menu photos turns out to be a bottleneck, swapping the
implementation in this one file is the place to do it - nothing else in the
extraction pipeline needs to change.
"""
from __future__ import annotations

import io

from PIL import Image

from src.utils.logging_utils import get_logger

log = get_logger(__name__)

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False


def ocr_image_bytes(image_bytes: bytes, lang: str = "deu+eng") -> tuple[str, float]:
    """Returns (text, confidence 0-1). Confidence is Tesseract's mean word
    confidence rescaled to 0-1; menus are short so this is a rough signal,
    not a precise metric - treat it as "better than nothing" for triage."""
    if not TESSERACT_AVAILABLE:
        log.warning("pytesseract/tesseract not installed - OCR skipped")
        return "", 0.0

    try:
        image = Image.open(io.BytesIO(image_bytes))
        data = pytesseract.image_to_data(image, lang=lang, output_type=pytesseract.Output.DICT)
        words = [w for w in data["text"] if w.strip()]
        confidences = [int(c) for c, w in zip(data["conf"], data["text"]) if w.strip() and c != "-1"]
        text = " ".join(words)
        avg_conf = (sum(confidences) / len(confidences) / 100.0) if confidences else 0.0
        return text, avg_conf
    except Exception as exc:  # noqa: BLE001 - OCR on real-world images fails in many ways
        log.warning("OCR failed: %s", exc)
        return "", 0.0
