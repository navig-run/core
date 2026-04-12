"""Shared OCR helpers."""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)


def extract_ocr_text_from_image_bytes(file_bytes: bytes) -> str | None:
    """Best-effort OCR text extraction from image bytes.

    Returns ``None`` when OCR is unavailable or extracted text is too short.
    """
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore

        img = Image.open(io.BytesIO(file_bytes))
        text = pytesseract.image_to_string(img).strip()
        return text if len(text) >= 3 else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("OCR extraction failed: %s", exc)
        return None
