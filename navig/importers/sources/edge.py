from __future__ import annotations

from ..utils import edge_default_path
from .chrome import ChromeImporter


class EdgeImporter(ChromeImporter):
    SOURCE_NAME = "edge"

    def default_path(self) -> str | None:
        return edge_default_path()
