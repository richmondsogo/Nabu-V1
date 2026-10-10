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
from pathlib import Path
import sys
import time

# Ensure src directory is on sys.path for direct script execution
_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
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

# Silence httpx and httpcore logging to prevent Telegram bot tokens and query params from leaking
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Registry for search query hashes used in refresh button callbacks:
# qhash -> (raw_query, last_refreshed_at)
_query_registry: dict[str, tuple[str, float]] = {}

# Hit / miss counters for /status
_stats = {
    "cache_hits": 0,
    "local_hits": 0,
    "upstream_requests": 0,
    "started_at": time.time(),
}


def _format_uptime(started_at: float) -> str:
    """Format elapsed time in hours, minutes, and seconds."""
    elapsed = max(0, int(time.time() - started_at))
    hours, rem = divmod(elapsed, 3600)
    mins, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h {mins}m {secs}s"
    return f"{mins}m {secs}s"


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
        "📚 <b>Welcome to Nabu — Book Discovery & Link Resolver</b>\n\n"
        "<blockquote>"
        "Send any book title, author, or ISBN to search.\n"
        "Tap a book result to get a direct one-click download link."
        "</blockquote>\n\n"
        "💡 <b>Frequently Asked Questions (FAQ):</b>\n"
        "• <b>Why did my download link expire or fail to start?</b>\n"
        "  Direct download keys are temporary (valid for a few minutes). "
        "If a download link expires or fails to start, simply tap the book card again in Telegram to get a fresh link.\n\n"
        "• <b>What if upstream sources are offline?</b>\n"
        "  Nabu automatically falls back to cached and offline catalogue results, labeled as degraded.\n\n"
        "⚡ <b>Commands:</b>\n"
        "• /help, /faq — Show this usage guide and FAQ\n"
        "• /status — System health & cache statistics\n"
        "• /mirrors — Upstream mirror status & latencies\n"
        "• /rebuild — Rebuild local search index"
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

    stats = await db.get_stats()
    total_reqs = _stats["cache_hits"] + _stats["upstream_requests"]
    hit_ratio = f"{(_stats['cache_hits'] / total_reqs * 100):.1f}%" if total_reqs > 0 else "0.0%"

    status_text = (
        "📊 <b>Nabu Status</b>\n\n"
        "<blockquote>"
        f"⏱️ Uptime: <b>{_format_uptime(_stats.get('started_at', time.time()))}</b>\n"
        f"📚 Catalog books: <b>{stats.books_count:,}</b>\n"
        f"🔍 Cached searches: <b>{stats.search_cache_count:,}</b>\n"
        f"💾 Database size: <b>{format_file_size(stats.db_size_bytes)}</b> (WAL: <b>{format_file_size(stats.wal_size_bytes)}</b>)\n"
        f"🌐 Active mirrors: <b>{active_count}/{len(mirrors)}</b>"
        "</blockquote>\n\n"
        "📈 <b>Traffic & Performance:</b>\n"
        f"• Cache hits: <b>{_stats['cache_hits']:,}</b> (<code>{hit_ratio}</code>)\n"
        f"• Upstream scrapes: <b>{_stats['upstream_requests']:,}</b>\n"
        f"• Degraded hits: <b>{_stats['local_hits']:,}</b>"
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
            cooldown_str = f" ⏳ <i>[cooling: {remaining}s]</i>"

        latency_str = f"<code>{m.latency_ms}ms</code>" if m.latency_ms else "<code>unknown</code>"
        lines.append(
            f"{status_icon} <b>{escape(m.url)}</b> <i>(fork: {m.fork})</i>\n"
            f"   ⚡ Latency: {latency_str} · Failures: {m.fail_count}{cooldown_str}"
        )
        if m.last_error:
            lines.append(f"   ⚠️ <i>Error: {escape(m.last_error[:60])}</i>")
        lines.append("")

    msg = "\n".join(lines).strip()
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
        pruned = await db.prune_expired_cache()
        await db.wal_checkpoint("TRUNCATE")
        logger.info("[rebuild] FTS rebuilt, pruned %d expired cache entries, WAL checkpointed", pruned)
        if update.effective_message:
            await update.effective_message.reply_text("✅ Search index rebuilt successfully.")
    except Exception as exc:
        logger.error("Failed to rebuild FTS: %s", exc, exc_info=True)
        if update.effective_message:
            await update.effective_message.reply_text("⚠️ Failed to rebuild search index.")


# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# Handlers: Text Search & Presentation
# -----------------------------------------------------------------------------

def _source_label(
    source: str,
    mirror_url: str | None,
    latency_ms: int | None,
    degraded: bool = False,
) -> str:
    mirror_name = ""
    if mirror_url:
        mirror_name = mirror_url.replace("https://", "").replace("http://", "").split("/")[0]
    latency_str = f", {latency_ms}ms" if latency_ms is not None else ""
    mirror_info = f" ({mirror_name}{latency_str})" if mirror_name else ""

    if degraded:
        if source == "cache":
            return "stale cache (degraded)"
        elif source == "local":
            return "offline catalog (degraded)"
        return f"{source} (degraded)"

    if source == "cache":
        return f"cache{mirror_info}"
    elif source == "local":
        return "local catalog"
    elif source == "upstream":
        return f"live upstream{mirror_info}"
    elif source == "mixed":
        return f"catalog + upstream{mirror_info}"
    return f"{source}{mirror_info}"


def _format_empty_results_text(query: str, upstream_reached: bool = False) -> str:
    upstream_status = "Live upstream sources were queried." if upstream_reached else "Local catalog was queried."
    return (
        f'No books found for <b>"{escape(query)}"</b>.\n\n'
        f"<i>{upstream_status}</i>\n\n"
        "<b>Suggestions:</b>\n"
        "• Check spelling of title and author\n"
        "• Search by author's last name only\n"
        "• Try fewer or broader keywords\n"
        "• Tap 🔄 Refresh if sources were temporarily busy"
    )


def _format_icon(file_type: str | None) -> str:
    """Return a format-specific emoji icon for books."""
    ft = (file_type or "").strip().lower()
    if ft == "epub":
        return "📘"
    elif ft == "pdf":
        return "📕"
    elif ft in ("mobi", "azw", "azw3"):
        return "📙"
    elif ft in ("djvu", "cbr", "cbz"):
        return "📗"
    return "📄"


def _filter_hits(hits: list[Book], active_filter: str = "all") -> list[Book]:
    filt = active_filter.strip().lower()
    if filt in ("", "all"):
        return hits
    return [b for b in hits if (b.file_type or "").strip().lower() == filt]


def _format_book_card_keyboard(book: Book, direct_url: str | None = None) -> InlineKeyboardMarkup | None:
    """Build interactive action buttons (direct download & mirror backups) for book cards."""
    if not book.md5:
        return None
    clean_md5 = book.md5.strip().lower()
    keyboard: list[list[InlineKeyboardButton]] = []
    if direct_url:
        keyboard.append([
            InlineKeyboardButton("⚡ Instant Download (One-Click)", url=direct_url)
        ])
        keyboard.append([
            InlineKeyboardButton("🌐 Libgen.li", url=f"https://libgen.li/ads.php?md5={clean_md5}"),
            InlineKeyboardButton("🌐 Libgen.is", url=f"https://libgen.is/book/index.php?md5={clean_md5}"),
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("🌐 Libgen.li Landing", url=f"https://libgen.li/ads.php?md5={clean_md5}"),
            InlineKeyboardButton("🌐 Libgen.is Landing", url=f"https://libgen.is/book/index.php?md5={clean_md5}"),
        ])
        keyboard.append([
            InlineKeyboardButton("🌐 Libgen.la Landing", url=f"https://libgen.la/ads.php?md5={clean_md5}"),
            InlineKeyboardButton("🌐 Anna's Archive", url=f"https://annas-archive.org/md5/{clean_md5}"),
        ])
    return InlineKeyboardMarkup(keyboard)


def _format_book_card(book: Book, direct_url: str | None = None) -> str:
    """Format a detailed book card HTML text with direct and backup links."""
    author_str = escape(book.author) if book.author else ""
    format_str = escape(book.file_type.upper()) if book.file_type else ""
    size_str = format_file_size(book.file_size)
    title_str = escape(book.title)
    icon = _format_icon(book.file_type)

    if not book.md5:
        download_block = "(no direct link)"
    else:
        clean_md5 = book.md5.strip().lower()
        if direct_url:
            download_block = (
                "<b>Download:</b>\n"
                f'• <a href="{direct_url}"><b>⚡ Direct Download (One-Click)</b></a>\n'
                f'• <a href="https://libgen.la/ads.php?md5={clean_md5}">Libgen.la (Backup)</a>\n'
                f'• <a href="https://libgen.is/book/index.php?md5={clean_md5}">Libgen.is (Backup)</a>\n\n'
                "<i>Link is temporary. Tap the book again for a fresh one.</i>"
            )
        else:
            links = build_links(book.md5)
            links_formatted = "\n".join(f'• <a href="{url}"><b>{escape(label)}</b></a>' for label, url in links)
            download_block = (
                "<i>⚠️ Direct download link unavailable; using landing page links:</i>\n\n"
                f"<b>Download links:</b>\n{links_formatted}\n\n"
                "<i>Tap a link to download in your browser.</i>"
            )

    spec_parts = []
    if format_str:
        spec_parts.append(f"Format: <b>{format_str}</b>")
    if size_str and size_str != "Unknown size":
        spec_parts.append(f"Size: <b>{size_str}</b>")

    spec_blockquote = f"<blockquote>{' · '.join(spec_parts)}</blockquote>" if spec_parts else ""
    author_line = f"\n👤 <b>Author:</b> {author_str}" if author_str else ""

    return (
        f"{icon} <b>{title_str}</b>"
        f"{author_line}\n"
        f"{spec_blockquote}\n\n"
        f"{download_block}"
    ).strip()


def _format_results_text(
    outcome: SearchOutcome,
    page: int = 1,
    page_size: int = 8,
    active_filter: str = "all",
) -> str:
    filtered_hits = _filter_hits(outcome.hits, active_filter)
    total = len(filtered_hits)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    end = min(start + page_size, total)
    page_hits = filtered_hits[start:end]

    q_display = escape(outcome.query_normalized or "")
    source_str = _source_label(outcome.source, outcome.mirror_url, outcome.latency_ms, outcome.degraded)
    degraded_note = (
        "\n<i>⚠️ Upstream sources unreachable. Showing offline/stale catalogue results (degraded).</i>"
        if outcome.degraded
        else ""
    )
    relaxed_note = (
        "\n<i>ℹ️ Strict query returned no results; showing relaxed search results.</i>"
        if outcome.is_relaxed
        else ""
    )
    filter_label = f" [{active_filter.upper()}]" if active_filter.lower() != "all" else ""

    lines = [
        f"🔍 <b>Search:</b> <code>{q_display}</code>{filter_label}",
        "<blockquote>"
        f"Found {total} result(s): Showing {start + 1 if total > 0 else 0}–{end} of {total} (Page {page}/{total_pages}) · {source_str}"
        "</blockquote>"
        f"{degraded_note}{relaxed_note}",
        "",
    ]

    if total == 0:
        lines.append(f"<i>No {active_filter.upper()} results found for this search. Tap [ALL] below to reset filter.</i>")
    else:
        for idx, b in enumerate(page_hits, start=start + 1):
            icon = _format_icon(b.file_type)
            meta_parts = []
            if b.file_type:
                meta_parts.append(escape(b.file_type.upper()))
            if b.file_size:
                meta_parts.append(format_file_size(b.file_size))
            spec_str = " · ".join(meta_parts)

            author_str = f"👤 {escape(b.author)}\n   " if b.author else ""
            spec_badge = f"📦 {spec_str}" if spec_str else ""
            lines.append(f"{idx}. {icon} <b>{escape(b.title)}</b>\n   {author_str}{spec_badge}\n")

    return "\n".join(lines).rstrip()


def _format_search_keyboard(
    hits: list[Book],
    qhash: str,
    page: int = 1,
    page_size: int = 8,
    active_filter: str = "all",
) -> InlineKeyboardMarkup:
    """Construct inline buttons for book results with Prev/Next pagination, format filters, and refresh."""
    filtered_hits = _filter_hits(hits, active_filter)
    total = len(filtered_hits)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    end = min(start + page_size, total)
    page_hits = filtered_hits[start:end]

    buttons = []
    for idx, b in enumerate(page_hits, start=start + 1):
        ext_icon = _format_icon(b.file_type)
        author_part = f" — {b.author}" if b.author else ""
        raw_label = f"{idx}. {ext_icon} {b.title}{author_part}"
        label = (raw_label[:57] + "...") if len(raw_label) > 60 else raw_label
        buttons.append([InlineKeyboardButton(label, callback_data=encode_callback("book", b.id))])

    # Navigation buttons (Page Prev / Next)
    if total_pages > 1:
        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=encode_callback("page", qhash, page - 1, active_filter)))
        nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data=encode_callback("noop", qhash)))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=encode_callback("page", qhash, page + 1, active_filter)))
        buttons.append(nav_row)

    # Format Filter Buttons: [ALL], [EPUB], [PDF]
    af = active_filter.lower()
    btn_all = "🔘 ALL" if af == "all" else "⚪ ALL"
    btn_epub = "🔘 EPUB" if af == "epub" else "⚪ EPUB"
    btn_pdf = "🔘 PDF" if af == "pdf" else "⚪ PDF"
    filter_row = [
        InlineKeyboardButton(btn_all, callback_data=encode_callback("filter", qhash, "all")),
        InlineKeyboardButton(btn_epub, callback_data=encode_callback("filter", qhash, "epub")),
        InlineKeyboardButton(btn_pdf, callback_data=encode_callback("filter", qhash, "pdf")),
    ]
    buttons.append(filter_row)

    # Append refresh button
    buttons.append([InlineKeyboardButton("🔄 Refresh Sources", callback_data=encode_callback("refresh", qhash))])
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
        elif outcome.source in ("upstream", "mixed"):
            _stats["upstream_requests"] += 1

        if not outcome.hits:
            if update.effective_message:
                await update.effective_message.reply_text(
                    _format_empty_results_text(query, upstream_reached=outcome.upstream_reached),
                    parse_mode=ParseMode.HTML,
                )
            return

        text = _format_results_text(outcome, page=1, page_size=config.page_size)
        reply_markup = _format_search_keyboard(outcome.hits, qh, page=1, page_size=config.page_size)

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
    user_id = query.from_user.id if query.from_user else None

    if not is_authorized(user_id, config):
        await query.answer("Sorry, this bot is private.", show_alert=True)
        return

    search_service: SearchService = context.bot_data.get("search_service")

    # Acknowledge callback immediately to eliminate Telegram loading spinner
    await query.answer()

    action, args = decode_callback(query.data)

    if action == "book":
        if not args:
            return
        try:
            book_id = int(args[0])
        except (ValueError, TypeError):
            logger.warning("Invalid book ID in callback data: %r", args)
            return

        book = await db.get_book_by_id(book_id)
        if not book:
            if query.message:
                await query.message.reply_text("⚠️ Book record not found.")
            return

        direct_url: str | None = None
        if book.md5:
            clean_md5 = book.md5.strip().lower()
            mirror_mgr: MirrorManager | None = context.bot_data.get("mirror_manager")
            if mirror_mgr:
                try:
                    direct_url = await asyncio.wait_for(
                        mirror_mgr.resolve_direct_link(clean_md5),
                        timeout=6.0,
                    )
                except asyncio.TimeoutError:
                    logger.debug("[bot] Direct link resolution timed out for %s, falling back to landing links", clean_md5)
                except Exception as exc:
                    logger.warning("[bot] Direct link resolution failed for %s: %s", clean_md5, exc)

        msg_html = _format_book_card(book, direct_url=direct_url)
        card_markup = _format_book_card_keyboard(book, direct_url=direct_url)

        if query.message:
            await query.message.reply_text(
                msg_html,
                reply_markup=card_markup,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

    elif action == "filter":
        if len(args) < 2:
            return
        qh = args[0]
        filt = args[1].lower()
        reg_entry = _query_registry.get(qh)
        if not reg_entry:
            await query.answer("Search expired. Please search again.", show_alert=True)
            return

        raw_query, _ = reg_entry
        qn = normalize_query(raw_query)
        try:
            cached = await db.get_search_cache(qn)
            if not cached:
                outcome = await search_service.search_books(raw_query, user_id=user_id)
            else:
                book_ids = cached[0]
                books = await db.get_books_by_ids(book_ids)
                outcome = SearchOutcome(
                    hits=books,
                    source="cache",
                    total_count=len(books),
                    query_normalized=qn,
                )

            text = _format_results_text(outcome, page=1, page_size=config.page_size, active_filter=filt)
            reply_markup = _format_search_keyboard(outcome.hits, qh, page=1, page_size=config.page_size, active_filter=filt)
            if query.message:
                await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.debug("Failed editing message for filter change: %s", e)

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
                    await query.message.reply_text(
                        _format_empty_results_text(raw_query, upstream_reached=outcome.upstream_reached),
                        parse_mode=ParseMode.HTML,
                    )
                return

            text = _format_results_text(outcome, page=1, page_size=config.page_size)
            reply_markup = _format_search_keyboard(outcome.hits, qh, page=1, page_size=config.page_size)
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

    elif action == "page":
        if len(args) < 2:
            return
        qh = args[0]
        try:
            page_num = int(args[1])
        except (ValueError, TypeError):
            return
        filt = args[2].lower() if len(args) > 2 else "all"

        reg_entry = _query_registry.get(qh)
        if not reg_entry:
            await query.answer("Search expired. Please search again.", show_alert=True)
            return

        raw_query, _ = reg_entry
        qn = normalize_query(raw_query)
        try:
            cached = await db.get_search_cache(qn)
            if not cached:
                outcome = await search_service.search_books(raw_query, user_id=user_id)
            else:
                book_ids = cached[0]
                books = await db.get_books_by_ids(book_ids)
                outcome = SearchOutcome(
                    hits=books,
                    source="cache",
                    total_count=len(books),
                    query_normalized=qn,
                )

            text = _format_results_text(outcome, page=page_num, page_size=config.page_size, active_filter=filt)
            reply_markup = _format_search_keyboard(outcome.hits, qh, page=page_num, page_size=config.page_size, active_filter=filt)
            if query.message:
                await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        except RateLimitedError:
            await query.answer("Slow down! Please wait a few seconds before searching again.", show_alert=True)
        except AllMirrorsFailed:
            if query.message:
                await query.message.reply_text("⚠️ All sources unreachable. Try again later.")
        except Exception as e:
            logger.debug("Failed editing message for page navigation: %s", e)

    elif action == "noop":
        await query.answer()


async def handle_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline queries (@bot <query>) for fast, seamless search sharing."""
    inline_query = update.inline_query
    if not inline_query:
        return

    config: Config = context.bot_data["config"]
    search_service: SearchService = context.bot_data["search_service"]
    user_id = inline_query.from_user.id if inline_query.from_user else None

    if not is_authorized(user_id, config):
        await inline_query.answer([], is_personal=True, cache_time=5)
        return

    query_text = inline_query.query.strip()
    if not query_text:
        await inline_query.answer([], is_personal=True, cache_time=5)
        return

    try:
        outcome = await search_service.search_books(query_text, user_id=user_id)
        results: list[InlineQueryResultArticle] = []
        for i, book in enumerate(outcome.hits[:20]):
            card_text = _format_book_card(book)
            card_markup = _format_book_card_keyboard(book)
            desc_parts = []
            if book.author:
                desc_parts.append(book.author)
            if book.file_type:
                desc_parts.append(f"[{book.file_type.upper()}]")
            if book.file_size:
                desc_parts.append(format_file_size(book.file_size))
            desc = " · ".join(desc_parts)

            results.append(
                InlineQueryResultArticle(
                    id=f"{book.id or i}_{i}",
                    title=book.title,
                    description=desc if desc else None,
                    reply_markup=card_markup,
                    input_message_content=InputTextMessageContent(
                        message_text=card_text,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    ),
                )
            )

        await inline_query.answer(results, cache_time=30, is_personal=True)
    except Exception as exc:
        logger.warning("Inline query failed for %r: %s", query_text, exc)
        await inline_query.answer([], is_personal=True, cache_time=5)


# -----------------------------------------------------------------------------
# Application Factory & Lifecycle
# -----------------------------------------------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log unhandled errors and notify user cleanly with zero data leakage."""
    err = context.error
    logger.error("Exception while handling an update: %r", err, exc_info=err)
    if isinstance(update, Update):
        try:
            if update.effective_message:
                await update.effective_message.reply_text("⚠️ An unexpected error occurred. Please try again.")
            elif update.callback_query:
                await update.callback_query.answer("⚠️ An error occurred. Please try again.", show_alert=True)
        except Exception as notify_err:
            logger.debug("Failed sending error notification to user: %r", notify_err)


async def _maintenance_loop(db: Database, interval_sec: float) -> None:
    """Periodically prune expired search cache entries and checkpoint WAL."""
    logger.info("Starting background maintenance loop (interval: %ss)", interval_sec)
    try:
        while True:
            await asyncio.sleep(interval_sec)
            try:
                pruned = await db.prune_expired_cache()
                cp = await db.wal_checkpoint("PASSIVE")
                logger.info(
                    "[maintenance] Pruned %d expired cache entries; WAL checkpoint: %s",
                    pruned,
                    cp,
                )
            except Exception as exc:
                logger.warning("[maintenance] Error during periodic maintenance: %r", exc)
    except asyncio.CancelledError:
        logger.info("Background maintenance loop cancelled.")


async def post_init(application: Application) -> None:
    """Launch background mirror probe and maintenance tasks once event loop is active."""
    mirror_manager: MirrorManager | None = application.bot_data.get("mirror_manager")
    if mirror_manager:
        application.bot_data["startup_probe_task"] = mirror_manager.startup_probe()

    db: Database | None = application.bot_data.get("db")
    config: Config | None = application.bot_data.get("config")
    if db:
        interval = getattr(config, "maintenance_interval_sec", 21600.0) if config else 21600.0
        application.bot_data["maintenance_task"] = asyncio.create_task(
            _maintenance_loop(db, interval)
        )


async def post_shutdown(application: Application) -> None:
    """Clean up background tasks, HTTP clients, and database handles on application shutdown to ensure zero leaks."""
    task: asyncio.Task | None = application.bot_data.get("startup_probe_task")
    if task and not task.done():
        logger.info("Cancelling background mirror probe task...")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    m_task: asyncio.Task | None = application.bot_data.get("maintenance_task")
    if m_task and not m_task.done():
        logger.info("Cancelling background maintenance task...")
        m_task.cancel()
        try:
            await m_task
        except asyncio.CancelledError:
            pass

    search_service: SearchService | None = application.bot_data.get("search_service")
    if search_service:
        await search_service.aclose()

    mirror_manager: MirrorManager | None = application.bot_data.get("mirror_manager")
    if mirror_manager:
        await mirror_manager.aclose()

    db: Database | None = application.bot_data.get("db")
    if db:
        try:
            await db.wal_checkpoint("TRUNCATE")
        except Exception as exc:
            logger.warning("Failed WAL checkpoint on shutdown: %r", exc)


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

    app = (
        ApplicationBuilder()
        .token(config.telegram_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.bot_data["config"] = config
    app.bot_data["db"] = db
    app.bot_data["mirror_manager"] = mirror_manager
    app.bot_data["search_service"] = search_service
    app.bot_data["user_rate_limiter"] = user_rate_limiter

    # Register error handler
    app.add_error_handler(error_handler)

    # Register handlers
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(CommandHandler("faq", handle_help))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("mirrors", handle_mirrors))
    app.add_handler(CommandHandler("rebuild", handle_rebuild))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(InlineQueryHandler(handle_inline_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_search))

    return app


def main() -> None:
    """Entry point for running Nabu bot."""
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        config = load_config()
    except Exception as exc:
        logger.critical("Failed loading configuration: %s", exc)
        sys.exit(1)

    app = build_application(config)

    logger.info("Starting Nabu Telegram bot polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
