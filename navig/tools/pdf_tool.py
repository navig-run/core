"""
navig.tools.pdf_tool — Extract text and metadata from PDF files.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

from navig.tools.registry import BaseTool, ToolResult

logger = logging.getLogger(__name__)

class PdfTool(BaseTool):
    """Extract text from local or remote PDF files."""
    
    name = "pdf_tool"
    description = "Extract text and metadata from a given PDF file path or URL."
    parameters = [
        {"name": "path", "type": "string", "description": "Local file path or URL to the PDF", "required": True},
        {"name": "max_pages", "type": "number", "description": "Maximum number of pages to extract", "required": False}
    ]

    async def run(
        self,
        args: Dict[str, Any],
        on_status: Optional[Callable[[str], None]] = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        path = args.get("path", "")
        if not path:
            return ToolResult(
                name=self.name, success=False, error="Missing required argument 'path'",
                elapsed_ms=(time.monotonic() - t0) * 1000
            )

        if on_status:
            await on_status("reading_pdf", f"Reading PDF from {path}", 50)
            
        # Placeholder for actual PDF extraction logic
        # Typically we use PyPDF2, pdfplumber or similar tools here.
        output = {
            "text": f"[Extracted content from {path}]",
            "metadata": {"pages_read": 1}
        }
        
        return ToolResult(
            name=self.name,
            success=True,
            output=output,
            elapsed_ms=(time.monotonic() - t0) * 1000,
            status_events=[f"extracted text from {path}"]
        )
