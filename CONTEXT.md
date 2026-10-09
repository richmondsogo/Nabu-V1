# Project Context

> This is the authoritative source of project/domain knowledge for this repository.
>
> Keep it synchronized with architectural decisions in `docs/adr/`.
>
> Agents should read this at the start of each session to avoid repeatedly
> rediscovering terminology, constraints, and intent.

---

## Product

A private, self-hosted Telegram bot (`Nabu`) for searching and delivering books retrieved from shadow libraries (Anna's Archive and Libgen), cached locally in SQLite (with FTS5) and pinned in a local IPFS/Kubo node.

## Users

A small, authorized private whitelist of users specified by numeric Telegram user IDs in `TELEGRAM_ALLOWED_USER_IDS`.

## Core Problem

Accessing shadow libraries directly via browser is fraught with broken mirrors, aggressive CAPTCHAs, waitlists, and intermittent connectivity. Nabu provides a private, automated Telegram interface that retrieves, validates, deduplicates, locally caches metadata, and pins files to a local IPFS node so future requests are instantaneous and persistent.

## Domain

- **Shadow Libraries:** Decentralized repositories of books, research papers, and periodicals. Anna's Archive functions as a metadata aggregator across Libgen and IPFS, frequently publishing direct IPFS Content Identifiers (CIDs). Libgen provides direct HTTP downloads and JSON/HTML search.
- **IPFS / Kubo:** InterPlanetary File System daemon exposing an HTTP RPC API on `http://127.0.0.1:5001`. Supports streaming content blocks via `/api/v0/cat`, pinning CIDs via `/api/v0/pin/add`, and adding new files via `POST /api/v0/add`.
- **Telegram Bot API:** Messaging platform interface operating via long-polling (`python-telegram-bot`). Constrained by a 50 MB (52,428,800 bytes) maximum document upload size and a 64-byte payload limit for inline keyboard `callback_data`.

## Important Terms

| Term | Definition |
|---|---|
| **Kubo** | The reference Go implementation of IPFS, running locally as an RPC daemon on port 5001. |
| **CID** | Content Identifier (IPFS hash, e.g. CIDv1 base32) uniquely identifying a file. |
| **Shadow Library** | Online open-access book archives (Anna's Archive and Library Genesis). |
| **Delivery Queue** | In-memory asyncio queue served by exactly 3 concurrent workers delivering cached/pinned books. |
| **Acquisition Queue** | Separate in-memory asyncio queue served by 2 concurrent workers performing remote searches, resolves, and downloads. |
| **Candidate Cache** | In-memory TTL cache storing uncommitted remote search results for inline keyboard navigation (`web:<token>:<index>`). |
| **Magic Bytes** | Initial file header bytes inspected to verify actual MIME types (PDF, EPUB, MOBI) before catalog insertion or Telegram delivery. |

## Actors

- **Whitelisted Telegram Users:** Send search queries, inspect results, and trigger book downloads.
- **Delivery Workers (3):** Stream files from local Kubo, validate size/type, upload documents to Telegram, and guarantee temp file cleanup.
- **Acquisition Workers (2):** Execute sequential scraping across sources, resolve download handles (CID or HTTP), fetch payloads, pin into Kubo, and insert rows into SQLite.
- **Kubo RPC Daemon:** Local service on localhost:5001 providing IPFS cat/add/pin operations.
- **External Book Mirrors:** Anna's Archive and Libgen web endpoints subject to polite rate limiting and rotation.

## Constraints

- **Python Runtime:** Pinned to Python 3.12 via `.venv` and `uv`.
- **No External Infrastructure:** No Redis, no Celery, no ORM, no web framework, no headless browser.
- **Upload Limit:** Maximum file size for Telegram delivery is 52,428,800 bytes (50 MB). Files exceeding this must be aborted and deleted mid-stream.
- **Kubo Retrieval Timeout:** 90-second hard deadline enforced via `asyncio.timeout(90)`.
- **Acquisition Timeout:** 300-second hard deadline covering entire search, resolve, download, and pin cycle.
- **Temporary File Hygiene:** Temporary files stream exclusively to `tmp/<job_id>.part` and must be deleted on every exit path in a `finally` block.
- **Access Control:** Every handler verifies `user_id in config.allowed_user_ids`. Unauthorized users receive zero details.

## Non-Goals

- Public bot deployment (strictly private whitelist).
- Multi-node clustering or distributed queues.
- Web UI, dashboard, or REST API.
- Storing full book binaries inside SQLite.
- Headless browser automation (Playwright/Selenium).

## Important Decisions

- [ADR 0001](file:///c:/Users/Richmond/Desktop/Codebase/Nabu-V1/docs/adr/0001-architecture.md): Shadow Library Ingestion, Local IPFS Pinning, and Dual-Queue Bot Architecture.
