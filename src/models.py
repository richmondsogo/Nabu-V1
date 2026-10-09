"""Data models for Nabu-V1.

Plain dataclasses representing domain entities, database records, search results,
and mirrors. No ORM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse


@dataclass(frozen=True)
class Book:
    id: int
    title: str
    author: str | None = None
    cid: str | None = None
    md5: str | None = None
    filename: str | None = None
    file_size: int | None = None
    file_type: str | None = None
    description: str | None = None
    created_at: str = ""
    source: str | None = None
    source_id: str | None = None
    acquired_at: str | None = None
    pinned: bool = False
    fetch_failures: int = 0
    last_fetch_error: str | None = None

    @property
    def has_cid(self) -> bool:
        """Returns True if the book has a valid CID and hasn't exceeded failure threshold."""
        return bool(self.cid and self.cid.strip() and self.fetch_failures < 3)


@dataclass(frozen=True)
class SearchHit:
    source: str
    source_id: str
    title: str
    author: str | None = None
    year: str | None = None
    language: str | None = None
    extension: str | None = None
    filesize: int | None = None
    detail_url: str | None = None
    cid: str | None = None
    md5: str | None = None
    raw: dict[str, Any] | None = None


@dataclass
class Mirror:
    id: int
    source: str
    url: str
    fork: str  # 'li' | 'is'
    enabled: bool = True
    fail_count: int = 0
    last_ok: str | None = None
    last_error: str | None = None
    cooldown_until: float | None = None
    latency_ms: int | None = None

    @property
    def host(self) -> str:
        return urlparse(self.url).netloc


@dataclass(frozen=True)
class SearchOutcome:
    hits: list[Book]
    source: Literal["cache", "local", "upstream"]
    degraded: bool = False

