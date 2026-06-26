"""
navig.tools.pdf_tool — Extract text and metadata from PDF files.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from navig.tools.registry import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class PdfTool(BaseTool):
    """Extract text from local or remote PDF files."""

    name = "pdf_tool"
    description = "Extract text and metadata from a given PDF file path or URL."
    parameters = [
        {
            "name": "path",
            "type": "string",
            "description": "Local file path or URL to the PDF",
            "required": True,
        },
        {
            "name": "max_pages",
            "type": "number",
            "description": "Maximum number of pages to extract",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: Callable[[str], None] | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        path = args.get("path", "")
        if not path:
            return ToolResult(
                name=self.name,
                success=False,
                error="Missing required argument 'path'",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        if on_status:
            await on_status("reading_pdf", f"Reading PDF from {path}", 50)

        # Real extraction: pypdf text with PyMuPDF+Tesseract OCR fallback for
        # scanned PDFs. Shared with the inbox universal extractor so behaviour
        # stays identical everywhere PDFs are read.
        import asyncio
        from pathlib import Path as _Path

        from navig.inbox.extract import _extract_pdf

        try:
            max_pages = int(args.get("max_pages") or 30)
        except (TypeError, ValueError):
            max_pages = 30

        errors: list[str] = []
        text, meta = await asyncio.to_thread(
            _extract_pdf, _Path(path), max_pages=max_pages, errors=errors
        )
        meta = {"pages_read": meta.get("pages", 0), "ocr_fallback": meta.get("ocr_fallback", False)}

        return ToolResult(
            name=self.name,
            success=bool(text) or not errors,
            output={"text": text, "metadata": meta},
            error="; ".join(errors) or None,
            elapsed_ms=(time.monotonic() - t0) * 1000,
            status_events=[f"extracted text from {path}"],
        )
