# Step 09: Anna's Archive Source Adapter

## Objective

Implement the primary shadow library adapter for Anna's Archive (`sources/annas.py`), incorporating multi-mirror rotation, CID-first resolution, slow queue polling, and optional fast download API key support.

## Scope

- `sources/annas.py`:
  - `AnnasSource(BaseSource)` subclass.
  - Multi-mirror rotation (`https://annas-archive.org`, `https://annas-archive.se`).
  - Search parsing (`parse_search_html`) extracting MD5, title, author, year, language, format extension, and file size.
  - **CID-First Resolution**: searches detail pages (`/md5/<md5>`) for published IPFS CIDs (`bafy...`, `Qm...`), returning `kind="cid"` to fetch through local Kubo and bypass HTTP entirely.
  - **Fast Download API**: queries `/dyn/api/fast_download.json` when `aa_api_key` is provided.
  - **Slow Queue Polling**: follows `/slow_download/...` links, politely polling countdown status without hammering the server.
  - Streaming download with mid-stream 50 MB byte limit enforcement and guaranteed `.part` unlinking on failure.
- `sources/__init__.py`:
  - Exported `AnnasSource` alongside `BaseSource`, `LibgenSource`, and `SourceResolver`.
- Static test fixtures:
  - `tests/fixtures/annas_search.html`
  - `tests/fixtures/annas_detail_cid.html`
  - `tests/fixtures/annas_detail_slow.html`
  - `tests/fixtures/annas_slow_page.html`
- Unit tests:
  - `tests/test_annas.py`: 8 unit tests covering HTML search parsing, mock transport search, CID-first resolution, fast API key resolution, slow queue polling, mirror rotation, streaming download with oversize abortion, and `SourceResolver` multi-source priority integration (`annas` primary -> `libgen` fallback).

## Verification

- Ran `scripts/verify.ps1`:
  - Git available: OK
  - No tracked secrets: OK
  - Python tests: OK (90 passed in 29.1s across all suites, 0 failed).
- All 8 Anna's Archive unit tests passed.

## Decisions

- **CID-First Priority**: When Anna's Archive lists an IPFS CID, `resolve()` returns `DownloadHandle(kind="cid")` so that `AcquisitionManager` pulls the file through the local Kubo node, pins it, and never initiates an HTTP download.
- **Polite Polling**: Slow download polling honors `POLITE_DELAY_MS` between requests and sleeps dynamically if a queue wait time is reported.
