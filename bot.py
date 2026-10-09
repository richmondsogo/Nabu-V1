"""Main Telegram bot application for Nabu-V1.

Implements:
- Telegram ApplicationBuilder using native asyncio
- Whitelist authorization check inside every handler
- Commands: /start, /help, /queue, /status, /get, /fetch, /sources, /acquire, /rebuild
- Plain text messages interpreted as catalog book searches with automatic shadow library acquisition
- Compact inline keyboard callbacks (book:<id>, web:<token>)
- 3-worker delivery pipeline streaming from local Kubo, validating magic bytes,
  enforcing 50 MB limits, and guaranteeing temp file cleanup
- 2-worker acquisition manager orchestrating shadow library resolution and ingestion
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import sys

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from acquirer import AcquisitionManager
from config import Config, load_config
from database import Database
from ipfs import (
    KuboClient,
    KuboOfflineError,
    KuboTimeoutError,
    OversizeFileError,
)
from models import DownloadJob
from queue_manager import QueueManager
from sources.annas import AnnasSource
from sources.libgen import LibgenSource
from sources.resolver import SourceResolver
from utils import (
    build_delivery_filename,
    candidates,
    decode_callback,
    encode_callback,
    format_caption,
    format_file_size,
    get_user_error,
    is_plausible_book_file,
)

logger = logging.getLogger(__name__)


def is_authorized(user_id: int | None, config: Config) -> bool:
    """Return True if user_id is in the configured whitelist."""
    return user_id is not None and user_id in config.allowed_user_ids


async def reject_unauthorized(update: Update) -> None:
    """Reply with the standard private bot rejection message."""
    if update.effective_message:
        await update.effective_message.reply_text(get_user_error("unauthorized"))
    elif update.callback_query:
        await update.callback_query.answer(get_user_error("unauthorized"), show_alert=True)


# -----------------------------------------------------------------------------
# Worker Download Processor
# -----------------------------------------------------------------------------

def create_download_processor(
    app: Application,
    db: Database,
    kubo: KuboClient,
    config: Config,
):
    """Factory returning the async handler executed by delivery queue workers."""

    async def process_download(job: DownloadJob) -> None:
        book = await db.get_book_by_id(job.book_id)
        if not book:
            logger.error("Job %s referenced non-existent book_id %d", job.job_id, job.book_id)
            await app.bot.send_message(
                chat_id=job.chat_id,
                text="⚠️ Book record could not be found in the catalog.",
            )
            return

        if not book.cid:
            await app.bot.send_message(
                chat_id=job.chat_id,
                text=get_user_error("missing_cid"),
            )
            return

        tmp_file = config.temp_dir / f"{job.job_id}.part"
        config.temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            logger.info("Starting Kubo retrieval for CID %s (job %s)", book.cid, job.job_id)
            bytes_written = await kubo.cat_file(
                cid=book.cid,
                dest_path=tmp_file,
                max_bytes=config.max_telegram_file_size,
                timeout=config.download_timeout,
            )

            # Inspect magic bytes before delivery
            valid, detected_or_reason = is_plausible_book_file(tmp_file, expected_ext=book.file_type)
            if not valid:
                logger.warning(
                    "Job %s file failed validation: %s (CID: %s)",
                    job.job_id,
                    detected_or_reason,
                    book.cid,
                )
                await db.record_fetch_failure(book.id, f"Invalid format: {detected_or_reason}")
                await app.bot.send_message(
                    chat_id=job.chat_id,
                    text=get_user_error("file_corrupt"),
                )
                return

            ext = detected_or_reason if detected_or_reason not in ("zip", "unknown") else (book.file_type or "pdf")
            delivery_filename = build_delivery_filename(book.title, book.author, ext)
            caption = format_caption(book.title, book.author, bytes_written)

            logger.info("Uploading %s (%d bytes) to chat %d", delivery_filename, bytes_written, job.chat_id)
            with open(tmp_file, "rb") as doc_file:
                await app.bot.send_document(
                    chat_id=job.chat_id,
                    document=doc_file,
                    filename=delivery_filename,
                    caption=caption,
                    write_timeout=120.0,
                    read_timeout=120.0,
                )
            logger.info("Delivery of job %s succeeded", job.job_id)

        except OversizeFileError:
            logger.warning("Job %s aborted: file exceeded Telegram 50 MB limit", job.job_id)
            await app.bot.send_message(chat_id=job.chat_id, text=get_user_error("too_large"))
        except KuboTimeoutError:
            logger.error("Job %s aborted: Kubo retrieval timed out after %ds", job.job_id, config.download_timeout)
            await db.record_fetch_failure(book.id, "Kubo retrieval timed out")
            await app.bot.send_message(chat_id=job.chat_id, text=get_user_error("kubo_timeout"))
        except KuboOfflineError:
            logger.error("Job %s aborted: Kubo daemon is offline", job.job_id)
            await app.bot.send_message(chat_id=job.chat_id, text=get_user_error("kubo_offline"))
        except Exception as exc:
            logger.exception("Unexpected error during delivery for job %s: %s", job.job_id, exc)
            await db.record_fetch_failure(book.id, str(exc))
            await app.bot.send_message(chat_id=job.chat_id, text=get_user_error("telegram_upload_error"))
        finally:
            # Guarantees zero orphaned temporary files on all paths
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except OSError as e:
                    logger.warning("Failed to unlink temp file %s: %s", tmp_file, e)

    return process_download


# -----------------------------------------------------------------------------
# Telegram Command Handlers
# -----------------------------------------------------------------------------

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    user = update.effective_user
    if not is_authorized(user.id if user else None, config):
        await reject_unauthorized(update)
        return

    text = (
        "📚 *Welcome to Nabu*\n\n"
        "I can search and deliver books directly to Telegram.\n\n"
        "• Send any title or author to search the library.\n"
        "• `/queue` — View download queue status.\n"
        "• `/status` — View system and Kubo health.\n"
        "• `/get <query>` — Force shadow library search and acquisition.\n"
        "• `/fetch <book_id>` — Fetch a local catalog book with no CID.\n"
        "• `/sources` — View shadow library status.\n"
        "• `/acquire` — View your active acquisitions.\n"
        "• `/rebuild` — Rebuild search index.\n"
        "• `/help` — Search tips."
    )
    if update.effective_message:
        await update.effective_message.reply_text(text, parse_mode="Markdown")


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    user = update.effective_user
    if not is_authorized(user.id if user else None, config):
        await reject_unauthorized(update)
        return

    text = (
        "🔍 *How to Search*\n\n"
        "Send plain text with titles, authors, or keywords:\n"
        "• `clean code`\n"
        "• `robert martin`\n"
        "• `design patterns gamma`\n\n"
        "Tap any result to queue an instant download.\n"
        "If a book is not in the local library, I will automatically find and acquire it for you!"
    )
    if update.effective_message:
        await update.effective_message.reply_text(text, parse_mode="Markdown")


async def queue_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    queue: QueueManager = context.bot_data["queue"]
    user = update.effective_user
    if not is_authorized(user.id if user else None, config):
        await reject_unauthorized(update)
        return

    stats = queue.stats()
    user_jobs = queue.get_user_pending_jobs(user.id) if user else []

    lines = [
        "📊 *Download Queue Status*",
        f"Active workers: {stats['active']}/{queue.max_workers}",
        f"Waiting in queue: {stats['queued']}",
    ]

    if user_jobs:
        lines.append("\n*Your pending downloads:*")
        for j in user_jobs:
            pos = queue.calculate_position(j.job_id)
            title = j.title or f"Book #{j.book_id}"
            lines.append(f"• [Position {pos}] {title}")
    else:
        lines.append("\nYou have no active or pending downloads.")

    if update.effective_message:
        await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    kubo: KuboClient = context.bot_data["kubo"]
    queue: QueueManager = context.bot_data["queue"]
    acquirer: AcquisitionManager | None = context.bot_data.get("acquirer")
    user = update.effective_user
    if not is_authorized(user.id if user else None, config):
        await reject_unauthorized(update)
        return

    kubo_online = await kubo.is_online()
    kubo_status = "OK" if kubo_online else "OFFLINE"
    stats = queue.stats()

    acq_workers = acquirer.max_workers if acquirer else 0
    acq_completed = acquirer.completed_count if acquirer else 0
    text = (
        "🤖 *System Status*\n"
        "Bot: online\n"
        "Database: OK\n"
        f"Kubo: {kubo_status}\n"
        f"Active downloads: {stats['active']}/{queue.max_workers}\n"
        f"Queue: {stats['queued']}\n"
        f"Acquisition workers: {acq_workers}\n"
        f"Acquisitions completed: {acq_completed}"
    )
    if update.effective_message:
        await update.effective_message.reply_text(text, parse_mode="Markdown")


async def get_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Force shadow library search and acquisition."""
    config: Config = context.bot_data["config"]
    acquirer: AcquisitionManager | None = context.bot_data.get("acquirer")
    user = update.effective_user
    if not is_authorized(user.id if user else None, config):
        await reject_unauthorized(update)
        return

    if not acquirer:
        if update.effective_message:
            await update.effective_message.reply_text("Acquisition subsystem is not enabled.")
        return

    query_text = " ".join(context.args or []).strip()
    if not query_text:
        if update.effective_message:
            await update.effective_message.reply_text("Usage: `/get <title or author>`", parse_mode="Markdown")
        return

    status_msg = await update.effective_message.reply_text(f'🔎 Searching shadow libraries for "{query_text}"…')

    async def update_status(text: str) -> None:
        try:
            await status_msg.edit_text(text)
        except Exception:
            pass

    chat_id = update.effective_chat.id if update.effective_chat else user.id
    await acquirer.enqueue(
        query=query_text,
        user_id=user.id,
        chat_id=chat_id,
        status_callback=update_status,
    )


async def fetch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Acquire a specific local catalog book that has no CID."""
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    queue: QueueManager = context.bot_data["queue"]
    acquirer: AcquisitionManager | None = context.bot_data.get("acquirer")
    user = update.effective_user
    if not is_authorized(user.id if user else None, config):
        await reject_unauthorized(update)
        return

    if not acquirer:
        if update.effective_message:
            await update.effective_message.reply_text("Acquisition subsystem is not enabled.")
        return

    args = context.args or []
    if not args:
        if update.effective_message:
            await update.effective_message.reply_text("Usage: `/fetch <book_id>`", parse_mode="Markdown")
        return

    try:
        book_id = int(args[0])
    except ValueError:
        if update.effective_message:
            await update.effective_message.reply_text("Invalid book ID.")
        return

    book = await db.get_book_by_id(book_id)
    if not book:
        if update.effective_message:
            await update.effective_message.reply_text("⚠️ Book not found in catalog.")
        return

    if book.has_cid:
        # Already has CID: enqueue directly to delivery queue
        title = f"{book.title} — {book.author}" if book.author else book.title
        ok, job, pos, msg = queue.enqueue(
            user_id=user.id,
            chat_id=update.effective_chat.id if update.effective_chat else user.id,
            book_id=book.id,
            title=title,
        )
        if not ok:
            if update.effective_message:
                await update.effective_message.reply_text(f"⚠️ {msg}")
            return
        if update.effective_message:
            await update.effective_message.reply_text(f"✅ Book already has CID. Added to delivery queue at position {pos}.")
        return

    status_msg = await update.effective_message.reply_text(f'🔎 Fetching from shadow libraries for "{book.title}"…')

    async def update_status(text: str) -> None:
        try:
            await status_msg.edit_text(text)
        except Exception:
            pass

    chat_id = update.effective_chat.id if update.effective_chat else user.id
    await acquirer.enqueue(
        query=book.title,
        user_id=user.id,
        chat_id=chat_id,
        md5=book.md5,
        status_callback=update_status,
    )


async def sources_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display per-source status."""
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    user = update.effective_user
    if not is_authorized(user.id if user else None, config):
        await reject_unauthorized(update)
        return

    sources = await db.get_sources()
    lines = ["📚 *Shadow Library Sources*"]
    for s in sources:
        enabled = "enabled" if s.get("enabled", 1) else "disabled"
        last_ok = s.get("last_ok") or "never"
        last_err = s.get("last_error") or "none"
        lines.append(f"• *{s['name']}* ({enabled})\n  Last OK: `{last_ok}`\n  Last Error: `{last_err}`")

    if update.effective_message:
        await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def acquire_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display user's active/waiting acquisitions."""
    config: Config = context.bot_data["config"]
    acquirer: AcquisitionManager | None = context.bot_data.get("acquirer")
    user = update.effective_user
    if not is_authorized(user.id if user else None, config):
        await reject_unauthorized(update)
        return

    if not acquirer:
        if update.effective_message:
            await update.effective_message.reply_text("Acquisition subsystem is not enabled.")
        return

    active_job_id = acquirer._user_active.get(user.id)
    waiting_jobs = acquirer._user_waiting.get(user.id, [])

    lines = ["📥 *Your Acquisitions*"]
    if active_job_id:
        lines.append(f"• Active job: `{active_job_id}`")
    if waiting_jobs:
        lines.append(f"• Waiting in queue: {len(waiting_jobs)}")
        for w in waiting_jobs:
            lines.append(f"  - {w.query}")
    if not active_job_id and not waiting_jobs:
        lines.append("You have no active or pending acquisitions.")

    if update.effective_message:
        await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def rebuild_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Rebuild the SQLite FTS5 search index."""
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    user = update.effective_user
    if not is_authorized(user.id if user else None, config):
        await reject_unauthorized(update)
        return

    await db.rebuild_fts()
    if update.effective_message:
        await update.effective_message.reply_text("✅ Successfully rebuilt the FTS5 catalog search index.")


# -----------------------------------------------------------------------------
# Search & Callback Message Handlers
# -----------------------------------------------------------------------------

async def search_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    acquirer: AcquisitionManager | None = context.bot_data.get("acquirer")
    user = update.effective_user
    if not is_authorized(user.id if user else None, config):
        await reject_unauthorized(update)
        return

    query_text = (update.effective_message.text or "").strip() if update.effective_message else ""
    if not query_text:
        return

    # 1. Search local SQLite catalog
    hits = await db.search_books(query_text, limit=5)

    if not hits:
        # Cache query token for manual retry if needed
        token = candidates.store(user.id if user else 0, [{"query": query_text}])

        if config.auto_acquire and acquirer:
            status_msg = await update.effective_message.reply_text(
                f'No books found in local catalog for "{query_text}".\n\n🔎 Searching shadow libraries…'
            )

            async def update_status(text: str) -> None:
                try:
                    await status_msg.edit_text(text)
                except Exception:
                    pass

            chat_id = update.effective_chat.id if update.effective_chat else user.id
            await acquirer.enqueue(
                query=query_text,
                user_id=user.id,
                chat_id=chat_id,
                status_callback=update_status,
            )
            return

        # Auto acquire disabled or no acquirer: present button to search online
        cb_web = encode_callback("web", token)
        keyboard = [[InlineKeyboardButton("🌐 Search Anna's Archive and Libgen", callback_data=cb_web)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.effective_message:
            await update.effective_message.reply_text(
                f'No books found for "{query_text}".',
                reply_markup=reply_markup,
            )
        return

    # 2. Local hits found: build inline keyboard
    keyboard: list[list[InlineKeyboardButton]] = []
    for book in hits:
        author_str = f" — {book.author}" if book.author else ""
        button_text = f"{book.title}{author_str}"
        if len(button_text) > 48:
            button_text = button_text[:45] + "…"
        cb_data = encode_callback("book", book.id)
        keyboard.append([InlineKeyboardButton(button_text, callback_data=cb_data)])

    # If fewer than 5 local hits, offer "More results" from shadow libraries
    if len(hits) < 5:
        token = candidates.store(user.id if user else 0, [{"query": query_text}])
        cb_web = encode_callback("web", token)
        keyboard.append(
            [InlineKeyboardButton("🌐 More results from Anna's Archive and Libgen", callback_data=cb_web)]
        )

    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.effective_message:
        await update.effective_message.reply_text("Search results:", reply_markup=reply_markup)


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    # Answer immediately to prevent Telegram client timeout
    await query.answer()

    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    queue: QueueManager = context.bot_data["queue"]
    acquirer: AcquisitionManager | None = context.bot_data.get("acquirer")
    user = update.effective_user

    if not is_authorized(user.id if user else None, config):
        await reject_unauthorized(update)
        return

    data = query.data or ""
    action, args = decode_callback(data)

    if action == "book":
        if not args:
            return
        try:
            book_id = int(args[0])
        except ValueError:
            return

        book = await db.get_book_by_id(book_id)
        if not book:
            if query.message:
                await query.message.reply_text("⚠️ Book could not be found.")
            return

        if not book.has_cid:
            if not acquirer:
                if query.message:
                    await query.message.reply_text("⚠️ Book has no CID and acquisition subsystem is not enabled.")
                return

            # Book exists locally but has no CID: trigger acquisition
            chat_id = query.message.chat_id if query.message else user.id
            status_msg = await query.message.reply_text(f'🔎 Fetching from shadow libraries for "{book.title}"…')

            async def update_status(text: str) -> None:
                try:
                    await status_msg.edit_text(text)
                except Exception:
                    pass

            await acquirer.enqueue(
                query=book.title,
                user_id=user.id,
                chat_id=chat_id,
                md5=book.md5,
                status_callback=update_status,
            )
            return

        if book.file_size and book.file_size > config.max_telegram_file_size:
            if query.message:
                await query.message.reply_text(get_user_error("too_large"))
            return

        chat_id = query.message.chat_id if query.message else user.id
        title = f"{book.title} — {book.author}" if book.author else book.title
        ok, job, pos, msg = queue.enqueue(
            user_id=user.id,
            chat_id=chat_id,
            book_id=book.id,
            title=title,
        )

        if not ok:
            if query.message:
                await query.message.reply_text(f"⚠️ {msg}")
            return

        reply_msg = (
            "⏳ Added to the download queue.\n\n"
            f"Position: {pos}\n\n"
            "Book:\n"
            f"{title}"
        )
        if query.message:
            await query.message.reply_text(reply_msg)

    elif action in ("web", "more"):
        if not args:
            return
        if not acquirer:
            if query.message:
                await query.message.reply_text("⚠️ Acquisition subsystem is not enabled.")
            return

        token = args[0]
        cached_items = candidates.get_all(user.id if user else 0, token)
        search_query = cached_items[0].get("query", "") if cached_items else ""
        if not search_query:
            if query.message:
                await query.message.reply_text("⚠️ Search session expired. Please send your query again.")
            return

        chat_id = query.message.chat_id if query.message else user.id
        status_msg = await query.message.reply_text(f'🔎 Searching shadow libraries for "{search_query}"…')

        async def update_status(text: str) -> None:
            try:
                await status_msg.edit_text(text)
            except Exception:
                pass

        await acquirer.enqueue(
            query=search_query,
            user_id=user.id,
            chat_id=chat_id,
            status_callback=update_status,
        )


# -----------------------------------------------------------------------------
# Bot Application Builder & Lifecycle
# -----------------------------------------------------------------------------

def build_application(
    config: Config,
    db: Database | None = None,
    kubo: KuboClient | None = None,
    queue: QueueManager | None = None,
    resolver: SourceResolver | None = None,
    acquirer: AcquisitionManager | None = None,
) -> Application:
    """Build and configure the Telegram Application instance with full pipeline."""
    app = ApplicationBuilder().token(config.telegram_token).build()

    active_db = db or Database(config.db_path)
    active_kubo = kubo or KuboClient(
        api_url=config.ipfs_api_url,
        public_gateways=config.public_gateways,
        download_timeout=config.download_timeout,
        max_file_size=config.max_telegram_file_size,
    )
    processor = create_download_processor(app, active_db, active_kubo, config)
    active_queue = queue or QueueManager(handler=processor, max_workers=config.max_concurrent_downloads)

    # Initialize sources and resolver if not supplied
    if not resolver:
        annas = AnnasSource(
            mirrors=config.mirror_anna,
            api_key=config.aa_api_key,
            polite_delay_ms=config.polite_delay_ms,
            scrape_timeout=config.scrape_timeout,
        )
        libgen = LibgenSource(
            mirrors=config.mirror_libgen,
            polite_delay_ms=config.polite_delay_ms,
            scrape_timeout=config.scrape_timeout,
        )
        active_resolver = SourceResolver(
            sources={"annas": annas, "libgen": libgen},
            priority=config.source_priority,
        )
    else:
        active_resolver = resolver

    active_acquirer = acquirer or AcquisitionManager(
        db=active_db,
        kubo_client=active_kubo,
        resolver=active_resolver,
        delivery_queue=active_queue,
        max_workers=config.max_acquire_jobs,
        acquire_timeout=config.acquire_timeout,
        max_file_size=config.max_telegram_file_size,
        temp_dir=config.temp_dir,
    )

    # Store shared objects in bot_data
    app.bot_data["config"] = config
    app.bot_data["db"] = active_db
    app.bot_data["kubo"] = active_kubo
    app.bot_data["queue"] = active_queue
    app.bot_data["resolver"] = active_resolver
    app.bot_data["acquirer"] = active_acquirer

    # Register handlers
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("queue", queue_handler))
    app.add_handler(CommandHandler("status", status_handler))
    app.add_handler(CommandHandler("get", get_handler))
    app.add_handler(CommandHandler("fetch", fetch_handler))
    app.add_handler(CommandHandler("sources", sources_handler))
    app.add_handler(CommandHandler("acquire", acquire_handler))
    app.add_handler(CommandHandler("rebuild", rebuild_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_message_handler))
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    async def post_init(application: Application) -> None:
        logger.info("Initializing database schema...")
        await active_db.async_init_schema()

        kubo_online = await active_kubo.is_online()
        if kubo_online:
            logger.info("Kubo daemon verified online at %s", config.ipfs_api_url)
        else:
            logger.warning("Kubo daemon is OFFLINE at %s (running in degraded mode)", config.ipfs_api_url)

        active_queue.start()
        await active_acquirer.start()
        logger.info("Bot startup complete with delivery and acquisition queues")

    async def post_shutdown(application: Application) -> None:
        logger.info("Shutting down bot...")
        await active_acquirer.stop()
        await active_queue.stop()
        await active_resolver.close()
        await active_kubo.close()
        logger.info("Bot shutdown complete")

    app.post_init = post_init
    app.post_shutdown = post_shutdown

    return app


def main() -> None:
    """Main CLI entrypoint."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = load_config()
    app = build_application(config)
    logger.info("Starting Nabu Telegram bot polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
