"""Telegram Link Resolver Bot for Nabu-V1.

Implements high-throughput, zero-storage book discovery and link resolution:
- Whitelist authorization inside every handler.
- Cache-first search with bounded upstream SingleFlight.
- Instant callback resolution with multi-mirror links built purely from MD5.
- HTML parse mode with robust escaping.
- Health reporting and maintenance commands (/start, /help, /status, /mirrors, /rebuild).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
import time

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from concurrency import HostRateLimiter, SingleFlight, UserRateLimiter, create_global_semaphore
from config import Config, load_config
from database import Database
from models import Book, SearchOutcome
from search import AllMirrorsFailed, RateLimitedError, SearchService, normalize_query
from sources.mirror_manager import MirrorManager
from utils import build_links, decode_callback, encode_callback, escape, format_file_size

logger = logging.getLogger(__name__)

# Registry for search query hashes used in refresh button callbacks:
# qhash -> (raw_query, last_refreshed_at)
_query_registry: dict[str, tuple[str, float]] = {}

# Hit / miss counters for /status
_stats = {
    "cache_hits": 0,
    "local_hits": 0,
    "upstream_requests": 0,
}


def _qhash(query: str) -> str:
    """Generate compact 8-character hash for search queries."""
    qn = normalize_query(query)
    h = hashlib.sha256(qn.encode("utf-8")).hexdigest()[:8]
    return h


def is_authorized(user_id: int | None, config: Config) -> bool:
    """Check if user_id is in the configured whitelist."""
    return user_id is not None and user_id in config.allowed_user_ids


async def reject_unauthorized(update: Update) -> None:
    """Send private bot rejection message with zero data leakage."""
    msg = "Sorry, this bot is private."
    if update.effective_message:
        await update.effective_message.reply_text(msg)
    elif update.callback_query:
        await update.callback_query.answer(msg, show_alert=True)


# -----------------------------------------------------------------------------
# Handlers: Commands
# -----------------------------------------------------------------------------

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    user_id = update.effective_user.id if update.effective_user else None
    if not is_authorized(user_id, config):
        await reject_unauthorized(update)
        return

    welcome_text = (
        "📚 <b>Welcome to Nabu</b>\n\n"
        "Send me any book title or author to search.\n"
        "Tap a result to receive direct browser download links.\n\n"
        "<b>Commands:</b>\n"
        "/status — System health & cache statistics\n"
        "/mirrors — Upstream mirror status & latencies\n"
        "/rebuild — Rebuild search index"
    )
    if update.effective_message:
        await update.effective_message.reply_text(welcome_text, parse_mode=ParseMode.HTML)


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await handle_start(update, context)


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    user_id = update.effective_user.id if update.effective_user else None
    if not is_authorized(user_id, config):
        await reject_unauthorized(update)
        return

    mirrors = await db.get_all_mirrors("libgen")
    active_count = sum(1 for m in mirrors if m.enabled and (m.cooldown_until is None or m.cooldown_until <= time.time()))

    books_cnt = 0
    with db._get_connection() as conn:
        row = conn.execute("SELECT count(*) as cnt FROM books").fetchone()
        books_cnt = row["cnt"] if row else 0
        cache_row = conn.execute("SELECT count(*) as cnt FROM search_cache").fetchone()
        cache_cnt = cache_row["cnt"] if cache_row else 0

    status_text = (
        "📊 <b>Nabu Status</b>\n\n"
        f"📖 Catalog books: <b>{books_cnt}</b>\n"
        f"🔍 Cached searches: <b>{cache_cnt}</b>\n"
        f"🌐 Active mirrors: <b>{active_count}/{len(mirrors)}</b>\n\n"
        f"⚡ <b>Performance:</b>\n"
        f"• Cache hits: {_stats['cache_hits']}\n"
        f"• Local FTS hits: {_stats['local_hits']}\n"
        f"• Upstream scrapes: {_stats['upstream_requests']}"
    )
    if update.effective_message:
        await update.effective_message.reply_text(status_text, parse_mode=ParseMode.HTML)


async def handle_mirrors(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    user_id = update.effective_user.id if update.effective_user else None
    if not is_authorized(user_id, config):
        await reject_unauthorized(update)
        return

    mirrors = await db.get_all_mirrors("libgen")
    now = time.time()
    lines = ["🌐 <b>Configured Mirrors:</b>\n"]
    for m in mirrors:
        status_icon = "🟢" if m.enabled else "🔴"
        cooldown_str = ""
        if m.cooldown_until and m.cooldown_until > now:
            remaining = int(m.cooldown_until - now)
            status_icon = "🟡"
            cooldown_str = f" [cooling: {remaining}s]"

        latency_str = f"{m.latency_ms}ms" if m.latency_ms else "unknown"
        lines.append(
            f"{status_icon} <b>{escape(m.url)}</b> (fork: {m.fork})\n"
            f"   Latency: {latency_str} | Failures: {m.fail_count}{cooldown_str}"
        )
        if m.last_error:
            lines.append(f"   <i>Error: {escape(m.last_error[:60])}</i>")

    msg = "\n".join(lines)
    if update.effective_message:
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)


async def handle_rebuild(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    user_id = update.effective_user.id if update.effective_user else None
    if not is_authorized(user_id, config):
        await reject_unauthorized(update)
        return

    try:
        await db.rebuild_fts()
        if update.effective_message:
            await update.effective_message.reply_text("✅ Search index rebuilt successfully.")
    except Exception as exc:
        logger.error("Failed to rebuild FTS: %s", exc, exc_info=True)
        if update.effective_message:
            await update.effective_message.reply_text("⚠️ Failed to rebuild search index.")


# -----------------------------------------------------------------------------
# Handlers: Text Search
# -----------------------------------------------------------------------------

def _format_search_keyboard(hits: list[Book], qhash: str) -> InlineKeyboardMarkup:
    """Construct inline buttons for book results plus a refresh button."""
    buttons = []
    for b in hits:
        author_part = f" — {b.author}" if b.author else ""
        raw_label = f"{b.title}{author_part}"
        label = (raw_label[:57] + "...") if len(raw_label) > 60 else raw_label
        buttons.append([InlineKeyboardButton(label, callback_data=encode_callback("book", b.id))])

    # Append refresh button
    buttons.append([InlineKeyboardButton("🔄 Refresh from sources", callback_data=encode_callback("refresh", qhash))])
    return InlineKeyboardMarkup(buttons)


async def handle_text_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    search_service: SearchService = context.bot_data["search_service"]
    user_id = update.effective_user.id if update.effective_user else None

    if not is_authorized(user_id, config):
        await reject_unauthorized(update)
        return

    query = update.effective_message.text.strip() if update.effective_message and update.effective_message.text else ""
    if not query:
        return

    qh = _qhash(query)
    _query_registry[qh] = (query, _query_registry.get(qh, (query, 0.0))[1])

    try:
        outcome: SearchOutcome = await search_service.search_books(query, user_id=user_id)
        if outcome.source == "cache":
            _stats["cache_hits"] += 1
        elif outcome.source == "local":
            _stats["local_hits"] += 1
        elif outcome.source == "upstream":
            _stats["upstream_requests"] += 1

        if not outcome.hits:
            if update.effective_message:
                await update.effective_message.reply_text(f'No books found for "{escape(query)}".', parse_mode=ParseMode.HTML)
            return

        degraded_note = "\n<i>⚠️ Sources temporarily degraded; showing cached results.</i>" if outcome.degraded else ""
        text = f"Found {len(outcome.hits)} result(s):{degraded_note}"
        reply_markup = _format_search_keyboard(outcome.hits, qh)

        if update.effective_message:
            await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

    except RateLimitedError:
        if update.effective_message:
            await update.effective_message.reply_text("Slow down! Please wait a few seconds before searching again.")
    except AllMirrorsFailed:
        if update.effective_message:
            await update.effective_message.reply_text("⚠️ All sources unreachable. Try again later.")
    except Exception as exc:
        logger.error("Unhandled error during search for %r: %s", query, exc, exc_info=True)
        if update.effective_message:
            await update.effective_message.reply_text("⚠️ An error occurred while searching. Try again later.")


# -----------------------------------------------------------------------------
# Handlers: Callbacks
# -----------------------------------------------------------------------------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    search_service: SearchService = context.bot_data["search_service"]
    user_id = query.from_user.id if query.from_user else None

    if not is_authorized(user_id, config):
        await query.answer("Sorry, this bot is private.", show_alert=True)
        return

    # Acknowledge callback immediately to eliminate Telegram loading spinner
    await query.answer()

    action, args = decode_callback(query.data)

    if action == "book":
        if not args:
            return
        book_id = int(args[0])
        book = await db.get_book_by_id(book_id)
        if not book:
            if query.message:
                await query.message.reply_text("⚠️ Book record not found.")
            return

        # Format message template (HTML)
        author_str = escape(book.author) if book.author else "Unknown"
        format_str = escape(book.file_type.upper()) if book.file_type else "Unknown format"
        size_str = format_file_size(book.file_size)
        title_str = escape(book.title)

        links = build_links(book.md5)

        if links:
            links_formatted = "\n".join(f'• <a href="{url}">{escape(label)}</a>' for label, url in links)
            download_block = f"⬇️ <b>Download</b>\n{links_formatted}\n\n<i>Tap a link to download in your browser.</i>"
        else:
            download_block = "(no direct link)"

        msg_html = (
            f"📖 <b>{title_str}</b>\n"
            f"👤 {author_str}\n"
            f"📦 {format_str} · {size_str}\n\n"
            f"{download_block}"
        )

        if query.message:
            await query.message.reply_text(msg_html, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    elif action == "refresh":
        if not args:
            return
        qh = args[0]
        reg_entry = _query_registry.get(qh)
        if not reg_entry:
            await query.answer("Search expired. Please search again.", show_alert=True)
            return

        raw_query, last_refreshed_at = reg_entry
        now = time.time()
        if now - last_refreshed_at < config.refresh_cooldown:
            wait_s = int(config.refresh_cooldown - (now - last_refreshed_at))
            await query.answer(f"Refresh cooldown active. Please wait {wait_s}s.", show_alert=True)
            return

        _query_registry[qh] = (raw_query, now)
        try:
            outcome = await search_service.search_books(raw_query, user_id=user_id, force_upstream=True)
            _stats["upstream_requests"] += 1

            if not outcome.hits:
                if query.message:
                    await query.message.reply_text(f'No books found for "{escape(raw_query)}".', parse_mode=ParseMode.HTML)
                return

            degraded_note = "\n<i>⚠️ Sources temporarily degraded; showing cached results.</i>" if outcome.degraded else ""
            text = f"Refreshed results ({len(outcome.hits)}):{degraded_note}"
            reply_markup = _format_search_keyboard(outcome.hits, qh)
            if query.message:
                await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

        except RateLimitedError:
            await query.answer("Slow down! Please wait a few seconds before searching again.", show_alert=True)
        except AllMirrorsFailed:
            if query.message:
                await query.message.reply_text("⚠️ All sources unreachable. Try again later.")
        except Exception as exc:
            logger.error("Error refreshing %r: %s", raw_query, exc, exc_info=True)
            if query.message:
                await query.message.reply_text("⚠️ An error occurred while refreshing.")


# -----------------------------------------------------------------------------
# Application Factory
# -----------------------------------------------------------------------------

def build_application(config: Config) -> Application:
    """Build and wire the Telegram Application with all dependencies."""
    db = Database(config.db_path)
    db.init_schema()

    mirror_manager = MirrorManager(db, connect_timeout=config.connect_timeout)
    single_flight = SingleFlight()
    host_rate_limiter = HostRateLimiter(polite_delay_ms=config.polite_delay_ms)
    global_semaphore = create_global_semaphore(max_upstream=config.max_upstream)
    user_rate_limiter = UserRateLimiter(
        max_tokens=config.user_bucket_tokens,
        refill_per_sec=config.user_bucket_refill,
    )

    search_service = SearchService(
        db=db,
        config=config,
        mirror_manager=mirror_manager,
        single_flight=single_flight,
        host_rate_limiter=host_rate_limiter,
        global_semaphore=global_semaphore,
        user_rate_limiter=user_rate_limiter,
    )

    app = ApplicationBuilder().token(config.telegram_token).build()

    app.bot_data["config"] = config
    app.bot_data["db"] = db
    app.bot_data["mirror_manager"] = mirror_manager
    app.bot_data["search_service"] = search_service
    app.bot_data["user_rate_limiter"] = user_rate_limiter

    # Register handlers
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("mirrors", handle_mirrors))
    app.add_handler(CommandHandler("rebuild", handle_rebuild))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_search))

    return app


def main() -> None:
    """Entry point for running Nabu bot."""
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    try:
        config = load_config()
    except Exception as exc:
        logger.critical("Failed loading configuration: %s", exc)
        sys.exit(1)

    app = build_application(config)

    # Launch background mirror probe
    mirror_manager: MirrorManager = app.bot_data["mirror_manager"]
    mirror_manager.startup_probe()

    logger.info("Starting Nabu Telegram bot polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
