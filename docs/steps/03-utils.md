# Step 03: Utilities, File Sniffing, and Callback Management

## Objective

Implement file format sniffing via magic bytes, candidate caching for browsing uncommitted search results, compact callback encoding and decoding under 64 bytes, delivery filename sanitization, and canonical user-facing error message mappings.

## Scope

- Magic-byte sniffing (`sniff_file_type`, `is_plausible_book_file`) for PDF, EPUB, MOBI, AZW3, ZIP, and HTML detection.
- Callback encoding and decoding (`encode_callback`, `decode_callback`) with hard 64-byte validation.
- Per-user TTL-backed in-memory candidate cache (`CandidateCache`) for `web:<token>:<index>` callbacks.
- Safe filename sanitization (`sanitize_filename`, `build_delivery_filename`).
- Size and caption formatting (`format_file_size`, `format_caption`).
- Canonical user error mapping (`get_user_error`) according to GOAL.md specifications.
- Unit tests in `tests/test_utils.py`.

## Plan

1. Implement `utils.py` containing sniffing, candidate cache, callback encode/decode, filename sanitization, and error maps.
2. Ensure HTML error pages served by shadow library endpoints are explicitly rejected.
3. Guarantee callback strings never exceed 64 bytes.
4. Implement `tests/test_utils.py` covering all format sniffing paths, callback encoding, TTL sweep, and error messages.
5. Verify test suite and run `verify.ps1`.
6. Commit to branch `step-03-utils` and push to GitHub.

## Implementation

- Implemented `sniff_file_type` covering `%PDF-`, EPUB ZIP container headers with `mimetype`, PalmDOC `BOOKMOBI` signatures, Topaz `TPZ`, and HTML/XML error signatures.
- Implemented `CandidateCache` storing `(user_id, token) -> (hits, expires_at)` with 10-minute TTL and automatic sweep, ensuring browsing shadow libraries leaves zero unverified rows in SQLite.
- Callback strings validated strictly against `MAX_CALLBACK_BYTES = 64`.
- All 17 unit tests in `tests/test_utils.py` passed cleanly.

## Discoveries

- Shadow library mirrors often serve HTML error pages disguised with `.pdf` URLs upon 404, rate limit, or captcha challenges. Detecting `<!DOCTYPE`, `<html`, and `<?xml` early prevents writing corrupt files to disk.

## Verification

- Tests run: 36 passed (9 config, 10 database, 17 utils), 0 failed.
- Verification script: `scripts/verify.ps1` returned 3 passed, 0 failed.
- Working tree: Clean git status on `step-03-utils`.

## Diff / Checkpoint

- Branch: `step-03-utils`.
- Files created: `utils.py`, `tests/test_utils.py`, `docs/steps/03-utils.md`.

## Unresolved Issues

- None.

## Decisions

- Retained 8-character hex tokens for candidate cache (`secrets.token_hex(4)`), guaranteeing callback data like `web:a1b2c3d4:4` consumes only 14 bytes (well within the 64-byte limit).
