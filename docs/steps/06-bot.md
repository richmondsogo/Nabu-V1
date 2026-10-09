# Step 06: Telegram Bot, Handlers, Authorization, and Delivery Pipeline

## Objective

Implement the Telegram bot entrypoint (`bot.py`), command handlers (`/start`, `/help`, `/queue`, `/status`), search and inline callback routing, authorization checks inside every handler, and the end-to-end delivery pipeline streaming from local Kubo into chat documents.

## Scope

- `bot.py` using `python-telegram-bot` `ApplicationBuilder`.
- Whitelist authorization check inside every handler (`is_authorized`).
- Commands: `/start`, `/help`, `/queue`, `/status`.
- Plain text messages dispatched to local SQLite FTS5 search with inline keyboard results (`book:<id>`).
- Immediate `callback_query.answer()`, enqueueing into `QueueManager`, and position reporting.
- Delivery worker processor (`create_download_processor`): streams from Kubo `/api/v0/cat`, validates magic bytes, builds safe filename/caption, sends document to Telegram chat, and deletes `.part` files in `finally`.
- Error message mappings preventing internal stack trace leaks to users.
- Comprehensive end-to-end tests in `tests/test_bot.py`.

## Plan

1. Implement `bot.py` with modular handler functions and factory `build_application()`.
2. Implement delivery worker callback streaming from `KuboClient` with size/magic-byte checks.
3. Wire lifecycle hooks (`post_init`, `post_shutdown`).
4. Implement `tests/test_bot.py` verifying authorization, commands, search messages, callback buttons, delivery execution, and corrupt file aborts.
5. Run `verify.ps1`.
6. Commit on `step-06-bot`, push to GitHub, and open Pull Request.

## Implementation

- Whitelist checked directly in handlers: unauthorized requests receive `"Sorry, this bot is private."` without leaking search existence.
- Inline keyboard buttons are truncated to 48 chars for mobile Telegram display, carrying compact `book:<id>` callback data.
- Hand-inserted book test verified full delivery: Kubo streaming -> magic byte check -> `send_document` -> `.part` unlinked.
- At this step, the local-first loop is 100% complete: any hand-inserted or pre-seeded catalog book is immediately searchable, queueable, and deliverable via Telegram.

## Discoveries

- `python-telegram-bot` `Application.bot_data` serves as an ideal registry for passing typed `Config`, `Database`, `KuboClient`, and `QueueManager` to handlers without global state.

## Verification

- Tests run: 57 passed (7 bot, 9 config, 10 database, 9 ipfs, 5 queue, 17 utils), 0 failed.
- Verification script: `scripts/verify.ps1` returned 3 passed, 0 failed.
- Working tree: Clean git status on `step-06-bot`.

## Diff / Checkpoint

- Branch: `step-06-bot`.
- Files created: `bot.py`, `tests/test_bot.py`, `docs/steps/06-bot.md`.

## Unresolved Issues

- Remote sources (Libgen, Anna's Archive) and acquisition queue will be wired in Steps 7 through 9.

## Decisions

- Preserved strict delivery timeout (120s for document upload, 90s for Kubo cat).
