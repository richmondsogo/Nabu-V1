# Step 11: Transition to Zero-Storage Link Resolver Architecture (ADR 0002)

## Objective

Permanently transition Nabu from a file-delivery and IPFS-pinning bot into a high-throughput, zero-storage **Link Resolver**. The server stores metadata and URLs only, never files. Delivery delivers multi-mirror browser links constructed directly from MD5 hashes with zero network calls.

## Scope

- **Architecture Record (`docs/adr/0002-link-resolver-architecture.md`)**:
  - Accepted ADR superseding file delivery and Kubo IPFS pinning.
  - Documented Phase 0 verification gate results:
    - `/ads.php?md5=<md5>` confirmed 100% LIVE and 200 OK on `libgen.li`, `libgen.la`, and `libgen.bz`.
    - Canonical `is-fork` detail path identified as `/book/index.php?md5=<md5>` with `/md5/<md5>` rewrite alias.
    - Anna's Archive deferred to Phase 3.
- **Concurrency & Rate Limiting (`concurrency.py`)**:
  - `SingleFlight`: Deduplicates in-flight calls using background task; always pops key in `finally` block to prevent leaks; wrapped in `asyncio.shield` so caller cancellations do not abort in-flight work.
  - `GlobalSemaphore`: Bounds outbound upstream requests to `MAX_UPSTREAM=4`.
  - `HostRateLimiter`: Enforces per-host serialization and `POLITE_DELAY_MS=750` delay.
  - `UserRateLimiter`: Token bucket per Telegram user (5 tokens, 0.5 refill/sec), consumed on upstream search path only (zero tokens consumed on cache hits).
- **Dual-Fork Source Parsers (`sources/base.py`, `sources/libgen.py`)**:
  - Minimal `SourceParser` protocol.
  - `LiForkParser` (`libgen.li`, `libgen.la`, `libgen.bz`): `/index.php` search parser with standard library `HTMLParser` for clean title extraction; `/ads.php` detail links.
  - `IsForkParser` (`libgen.is`, `libgen.rs`): `/search.php` search parser; `/book/index.php` detail links.
  - Tolerates rows missing MD5 (`md5=None`).
- **Mirror Manager (`sources/mirror_manager.py`)**:
  - Non-blocking background startup probe.
  - Per-mirror latency tracking and exponential cooldown backoff (30s to 600s).
- **Search Orchestration (`search.py`)**:
  - Cache-first flow: `search_cache` (TTL=86400s) -> local FTS (threshold=3) -> `single_flight` scrape -> upsert & deduplicate -> cache write -> serve.
  - Graceful degradation: Serves stale cache or local FTS if all upstream mirrors fail.
- **Link Building & Telegram Bot (`utils.py`, `bot.py`)**:
  - `build_links(md5)` constructs up to 3 mirror links (`Libgen.li`, `Libgen.la`, `Libgen.is`).
  - Telegram bot rewritten for HTML parse mode with robust entity escaping.
  - Immediate callback acknowledgement (`await query.answer()`) eliminating loading spinners.
  - Zero-network callback resolution for `book:<id>`.
  - Command handlers: `/start`, `/help`, `/status`, `/mirrors`, `/rebuild`.
  - Lifecycle: `post_shutdown` hook cancelling background tasks cleanly on exit.
- **Project Layout Reorganization (`src/`)**:
  - Relocated all application source modules and packages from the root directory into `src/` (`src/bot.py`, `src/concurrency.py`, `src/config.py`, `src/database.py`, `src/importer.py`, `src/models.py`, `src/search.py`, `src/utils.py`, `src/sources/`).
  - Added `pyproject.toml` with `pythonpath = ["src"]` pytest configuration.
  - Added self-locating directory path initialization in CLI entrypoints (`src/bot.py`, `src/importer.py`).
- **Legacy Purge**:
  - Deleted `ipfs.py`, `queue_manager.py`, `sources/resolver.py`, `acquirer.py`, `sources/annas.py`, and legacy fixtures.
  - Cleaned up obsolete models from `models.py`.

## Verification

- **Automated Tests**:
  - 56 unit and integration tests passing (`pytest tests -v`).
  - `scripts/verify.ps1` clean (3 passed, 0 failed, 0 skipped).
- **Live Test Benchmark**:
  - Query: `"Designing Data-Intensive Applications"`.
  - Cold search: completed in 4.79s via upstream single-flight scrape.
  - Warm search: completed in 0.00ms (instant) from cache with zero outbound network calls.
  - Resolved MD5 `9f9b32db687e6c13a941acc13423d299`.
  - Probed links live: `Libgen.li` (200 OK, download button verified), `Libgen.la` (200 OK, download button verified).
  - Clean shutdown verified with zero orphaned background tasks.

## Decisions

- **Zero-Storage Link Delivery**: Dropped server-side downloading, file streaming, and IPFS pinning. Nabu serves URLs directly to the user's browser, eliminating 50 MB limits, disk exhaustion, and upstream server bottlenecks.
- **Multi-Mirror Redundancy**: Links are constructed across three distinct mirror domains to ensure accessibility regardless of ISP-level DNS or IP blocking.
