"""Base protocol for shadow library mirror parsers."""

from __future__ import annotations

from typing import Protocol
from models import Mirror, SearchHit


class SourceParser(Protocol):
    """Protocol for parsing mirror HTML search results and building URLs."""

    def search_url(self, mirror: Mirror, query: str) -> str:
        """Construct search URL for this mirror and query."""
        ...

    def detail_url(self, mirror: Mirror, md5: str) -> str:
        """Construct detail/download page URL for this mirror and md5."""
        ...

    def parse(self, html: str, mirror: Mirror) -> list[SearchHit]:
        """Parse HTML into SearchHit instances. If md5 is missing, md5=None."""
        ...
