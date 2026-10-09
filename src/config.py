"""Configuration loader and validation for Nabu-V1 Telegram Book Bot.

Loads settings from environment variables or .env file, validating all constraints
and failing fast on missing or invalid configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from dotenv import load_dotenv


class ConfigError(ValueError):
    """Raised when configuration values are missing, invalid, or malformed."""


@dataclass(frozen=True)
class Config:
    telegram_token: str
    allowed_user_ids: frozenset[int]
    db_path: Path
    # Link Resolver settings
    local_result_threshold: int = 3
    search_cache_ttl: float = 86400.0
    max_upstream: int = 4
    polite_delay_ms: int = 750
    connect_timeout: float = 8.0
    singleflight_timeout: float = 15.0
    result_limit: int = 50
    upstream_max_results: int = 50
    upstream_max_pages: int = 3
    page_size: int = 8
    empty_result_cache_ttl: float = 300.0
    user_bucket_tokens: float = 5.0
    user_bucket_refill: float = 0.5
    refresh_cooldown: float = 300.0
    mirror_libgen: tuple[str, ...] = (
        "https://libgen.li",
        "https://libgen.la",
        "https://libgen.bz",
        "https://libgen.is",
        "https://libgen.rs",
    )
    # Accepted but unused backwards-compatibility fields
    temp_dir: Path = Path("tmp")
    max_telegram_file_size: int = 52_428_800
    download_timeout: float = 90.0
    ipfs_api_url: str = "http://127.0.0.1:5001"
    max_concurrent_downloads: int = 3
    source_priority: tuple[str, ...] = ("libgen",)
    max_acquire_jobs: int = 2
    acquire_timeout: float = 300.0
    scrape_timeout: float = 20.0
    auto_acquire: bool = True
    mirror_anna: tuple[str, ...] = ("https://annas-archive.org", "https://annas-archive.se")
    aa_api_key: str = ""
    public_gateways: tuple[str, ...] = ("https://ipfs.io/ipfs", "https://dweb.link/ipfs")


def _validate_url(url: str, var_name: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        raise ConfigError(f"{var_name} cannot be empty")
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(f"{var_name} must be a valid http or https URL (got: {url!r})")
    return cleaned.rstrip("/")


def _parse_url_list(raw: str, var_name: str) -> tuple[str, ...]:
    urls = [u.strip() for u in raw.split(",") if u.strip()]
    if not urls:
        raise ConfigError(f"{var_name} must contain at least one valid URL")
    return tuple(_validate_url(u, var_name) for u in urls)


def _parse_int(raw: str | None, var_name: str, default: int, min_val: int = 1) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        val = int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{var_name} must be a valid integer (got: {raw!r})") from exc
    if val < min_val:
        raise ConfigError(f"{var_name} must be at least {min_val} (got: {val})")
    return val


def _parse_float(raw: str | None, var_name: str, default: float, min_val: float = 0.0) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        val = float(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{var_name} must be a valid number (got: {raw!r})") from exc
    if val < min_val:
        raise ConfigError(f"{var_name} must be at least {min_val} (got: {val})")
    return val


def _parse_bool(raw: str | None, default: bool = True) -> bool:
    if raw is None or not raw.strip():
        return default
    cleaned = raw.strip().lower()
    if cleaned in ("true", "1", "yes", "on"):
        return True
    if cleaned in ("false", "0", "no", "off"):
        return False
    raise ConfigError(f"Boolean value expected (got: {raw!r})")


def load_config(env: Mapping[str, str | None] | None = None, env_file: Path | str | None = ".env") -> Config:
    """Load, validate, and return the application Config.

    Fails fast with ConfigError if required variables are missing or malformed.
    """
    if env is None:
        load_dotenv(dotenv_path=env_file)
        raw_env: Mapping[str, str | None] = os.environ
    else:
        raw_env = env

    token = raw_env.get("TELEGRAM_TOKEN")
    if not token or not token.strip():
        raise ConfigError("TELEGRAM_TOKEN is required and cannot be empty")
    telegram_token = token.strip()

    raw_user_ids = raw_env.get("TELEGRAM_ALLOWED_USER_IDS")
    if not raw_user_ids or not raw_user_ids.strip():
        raise ConfigError(
            "TELEGRAM_ALLOWED_USER_IDS is required and cannot be empty. "
            "Specify at least one comma-separated Telegram numeric user ID."
        )
    allowed_ids: set[int] = set()
    for segment in raw_user_ids.split(","):
        s = segment.strip()
        if not s:
            continue
        try:
            uid = int(s)
            allowed_ids.add(uid)
        except ValueError as exc:
            raise ConfigError(
                f"TELEGRAM_ALLOWED_USER_IDS contains non-integer ID: {segment!r}"
            ) from exc

    if not allowed_ids:
        raise ConfigError(
            "TELEGRAM_ALLOWED_USER_IDS must contain at least one valid numeric Telegram user ID."
        )

    db_path = Path(raw_env.get("DB_PATH", "data/books.db").strip() or "data/books.db")
    temp_dir = Path(raw_env.get("TEMP_DIR", "tmp").strip() or "tmp")

    raw_ipfs_url = raw_env.get("IPFS_API_URL", "http://127.0.0.1:5001")
    ipfs_api_url = _validate_url(raw_ipfs_url, "IPFS_API_URL")

    # Link Resolver config items
    local_result_threshold = _parse_int(raw_env.get("LOCAL_RESULT_THRESHOLD"), "LOCAL_RESULT_THRESHOLD", 3)
    search_cache_ttl = _parse_float(raw_env.get("SEARCH_CACHE_TTL"), "SEARCH_CACHE_TTL", 86400.0)
    max_upstream = _parse_int(raw_env.get("MAX_UPSTREAM"), "MAX_UPSTREAM", 4)
    polite_delay_ms = _parse_int(raw_env.get("POLITE_DELAY_MS"), "POLITE_DELAY_MS", 750, min_val=0)
    connect_timeout = _parse_float(raw_env.get("CONNECT_TIMEOUT"), "CONNECT_TIMEOUT", 8.0)
    singleflight_timeout = _parse_float(raw_env.get("SINGLEFLIGHT_TIMEOUT"), "SINGLEFLIGHT_TIMEOUT", 15.0)

    # Result count & pagination settings
    raw_res_limit = raw_env.get("RESULT_LIMIT")
    default_max_results = 50
    if raw_res_limit is not None and raw_res_limit.strip():
        default_max_results = _parse_int(raw_res_limit, "RESULT_LIMIT", 50)
    upstream_max_results = _parse_int(
        raw_env.get("UPSTREAM_MAX_RESULTS"), "UPSTREAM_MAX_RESULTS", default_max_results
    )
    upstream_max_pages = _parse_int(raw_env.get("UPSTREAM_MAX_PAGES"), "UPSTREAM_MAX_PAGES", 3)
    page_size = _parse_int(raw_env.get("PAGE_SIZE"), "PAGE_SIZE", 8)
    empty_result_cache_ttl = _parse_float(
        raw_env.get("EMPTY_RESULT_CACHE_TTL"), "EMPTY_RESULT_CACHE_TTL", 300.0
    )
    result_limit = upstream_max_results

    user_bucket_tokens = _parse_float(raw_env.get("USER_BUCKET_TOKENS"), "USER_BUCKET_TOKENS", 5.0)
    user_bucket_refill = _parse_float(raw_env.get("USER_BUCKET_REFILL"), "USER_BUCKET_REFILL", 0.5)
    refresh_cooldown = _parse_float(raw_env.get("REFRESH_COOLDOWN"), "REFRESH_COOLDOWN", 300.0)

    # Backwards compatibility / unused fields
    max_downloads = _parse_int(raw_env.get("MAX_CONCURRENT_DOWNLOADS"), "MAX_CONCURRENT_DOWNLOADS", 3)
    download_timeout = _parse_float(raw_env.get("DOWNLOAD_TIMEOUT"), "DOWNLOAD_TIMEOUT", 90.0)
    max_tg_size = _parse_int(raw_env.get("MAX_TELEGRAM_FILE_SIZE"), "MAX_TELEGRAM_FILE_SIZE", 52_428_800)

    raw_priority = raw_env.get("SOURCE_PRIORITY", "libgen")
    source_priority = tuple(s.strip() for s in raw_priority.split(",") if s.strip()) or ("libgen",)

    max_acquire_jobs = _parse_int(raw_env.get("MAX_ACQUIRE_JOBS"), "MAX_ACQUIRE_JOBS", 2)
    acquire_timeout = _parse_float(raw_env.get("ACQUIRE_TIMEOUT"), "ACQUIRE_TIMEOUT", 300.0)
    scrape_timeout = _parse_float(raw_env.get("SCRAPE_TIMEOUT"), "SCRAPE_TIMEOUT", 20.0)
    auto_acquire = _parse_bool(raw_env.get("AUTO_ACQUIRE"), default=True)

    raw_anna_mirrors = raw_env.get("MIRROR_ANNA", "https://annas-archive.org,https://annas-archive.se")
    mirror_anna = _parse_url_list(raw_anna_mirrors, "MIRROR_ANNA")

    default_libgen = "https://libgen.li,https://libgen.la,https://libgen.bz,https://libgen.is,https://libgen.rs"
    raw_libgen_mirrors = raw_env.get("MIRROR_LIBGEN", default_libgen)
    mirror_libgen = _parse_url_list(raw_libgen_mirrors, "MIRROR_LIBGEN")

    aa_api_key = (raw_env.get("AA_API_KEY") or "").strip()

    raw_gateways = raw_env.get("PUBLIC_GATEWAYS", "https://ipfs.io/ipfs,https://dweb.link/ipfs")
    public_gateways = _parse_url_list(raw_gateways, "PUBLIC_GATEWAYS")

    return Config(
        telegram_token=telegram_token,
        allowed_user_ids=frozenset(allowed_ids),
        db_path=db_path,
        local_result_threshold=local_result_threshold,
        search_cache_ttl=search_cache_ttl,
        max_upstream=max_upstream,
        polite_delay_ms=polite_delay_ms,
        connect_timeout=connect_timeout,
        singleflight_timeout=singleflight_timeout,
        result_limit=result_limit,
        upstream_max_results=upstream_max_results,
        upstream_max_pages=upstream_max_pages,
        page_size=page_size,
        empty_result_cache_ttl=empty_result_cache_ttl,
        user_bucket_tokens=user_bucket_tokens,
        user_bucket_refill=user_bucket_refill,
        refresh_cooldown=refresh_cooldown,
        mirror_libgen=mirror_libgen,
        temp_dir=temp_dir,
        max_telegram_file_size=max_tg_size,
        download_timeout=download_timeout,
        ipfs_api_url=ipfs_api_url,
        max_concurrent_downloads=max_downloads,
        source_priority=source_priority,
        max_acquire_jobs=max_acquire_jobs,
        acquire_timeout=acquire_timeout,
        scrape_timeout=scrape_timeout,
        auto_acquire=auto_acquire,
        mirror_anna=mirror_anna,
        aa_api_key=aa_api_key,
        public_gateways=public_gateways,
    )
