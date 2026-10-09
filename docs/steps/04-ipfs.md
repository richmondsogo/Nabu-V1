# Step 04: IPFS / Kubo Async Client and Gateway Fallback

## Objective

Implement the asynchronous Kubo (IPFS) RPC client, streaming content retrieval via `/api/v0/cat`, hard 90-second deadline enforcement with `asyncio.timeout`, mid-stream byte counting with abortion on files exceeding 50 MB, ingest (`/api/v0/add`), pinning (`/api/v0/pin/add`), and public gateway fallback.

## Scope

- Async client `KuboClient` in `ipfs.py`.
- Hard 90-second timeout wrapping the entire retrieval lifecycle.
- Mid-stream byte limit enforcement (`max_file_size = 52_428_800` bytes).
- Ingest operation with `cid-version=1&raw-leaves=true&pin=true`.
- Pin operation for source-supplied CIDs.
- Public gateway fallback route for CIDs when local node is unavailable or times out.
- Unit tests in `tests/test_ipfs.py` using `httpx.MockTransport`.

## Plan

1. Implement `ipfs.py` using `httpx.AsyncClient` with reusable connection pools.
2. Implement streaming `cat_file()` with `asyncio.timeout(90)`.
3. Enforce immediate cancellation and partial file deletion upon timeout or oversize exceptions.
4. Implement `add_file()` with multipart streaming and CIDv1 format matching Anna's Archive.
5. Implement `pin_cid()` for pinning source-supplied CIDs.
6. Implement `_cat_gateway()` fallback across configured public gateways.
7. Test all operations, errors, and cancellations in `tests/test_ipfs.py`.
8. Run `scripts/verify.ps1`.
9. Commit to branch `step-04-ipfs`, push, and open Pull Request.

## Implementation

- Used Python 3.11+ native `asyncio.timeout` around `_cat_local` and `_cat_gateway`.
- Ensured any failure (timeout, oversize, connection error) unlinks the destination `.part` file in an immediate cleanup block.
- Implemented `add_file` with `cid-version=1&raw-leaves=true&pin=true` and `wrap-with-directory=false`.
- Tested against `httpx.MockTransport` with 9 unit tests covering normal retrieval, oversize aborts, timeout cancellation, health checks, pinning, and gateway fallbacks.

## Discoveries

- Streaming chunked responses via `resp.aiter_bytes()` allows byte counting before the whole file is buffered in RAM, preserving the non-negotiable memory limit constraint.
- `httpx.MockTransport` supports asynchronous generators in response content, enabling exact simulation of hung/slow streams for timeout testing without mock servers.

## Verification

- Tests run: 45 passed (9 config, 10 database, 17 utils, 9 ipfs), 0 failed.
- Verification script: `scripts/verify.ps1` returned 3 passed, 0 failed.
- Working tree: Clean git status on `step-04-ipfs`.

## Diff / Checkpoint

- Branch: `step-04-ipfs`.
- Files created: `ipfs.py`, `tests/test_ipfs.py`, `docs/steps/04-ipfs.md`.

## Unresolved Issues

- None.

## Decisions

- Gateway fallback retries the same streaming path and enforces the same 90-second deadline; it serves as transport redundancy rather than deadline relaxation.
