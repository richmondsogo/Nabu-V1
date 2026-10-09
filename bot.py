"""Main Telegram bot application for Nabu-V1.

Implements:
- Telegram ApplicationBuilder using native asyncio
- Whitelist authorization check inside every handler
- /start, /help, /queue, /status commands
- Plain text messages interpreted as catalog book searches
- Compact inline keyboard callbacks (book:<id>, more:<token>)
- 3-worker delivery pipeline streaming from local Kubo, validating magic bytes,
  enforcing 50 MB limits, and guaranteeing temp file cleanup
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
                await db.record_fetch_failure(book.id, f"Corrupted payload: {detected_or_reason}")
                await app.bot.send_message(
                    chat_id=job.chat_id,
                    text=get_user_error("file_corrupt"),
                )
                return

            # Determine delivery filename and caption
            filename = build_delivery_filename(book.title, book.author, detected_or_reason)
            caption = format_caption(book.title, book.author, bytes_written)

            # Upload document to Telegram
            logger.info("Delivering document %s to chat %d", filename, job.chat_id)
            with open(tmp_file, "rb") as f:
                await app.bot.send_document(
                    chat_id=job.chat_id,
                    document=f,
                    filename=filename,
                    caption=caption,
                    read_timeout=120.0,
                    write_timeout=120.0,
                )

        except (KuboTimeoutError, KuboOfflineError) as exc:
            logger.warning("Kubo retrieval failed for book %d (job %s): %s", book.id, job.job_id, exc)
            await db.record_fetch_failure(book.id, str(exc))
            await app.bot.send_message(
                chat_id=job.chat_id,
                text=get_user_error("kubo_timeout"),
            )
        except OversizeFileError:
            logger.warning("Book %d (job %s) exceeded Telegram size limit", book.id, job.job_id)
            await app.bot.send_message(
                chat_id=job.chat_id,
                text=get_user_error("too_large"),
            )
        except Exception as exc:
            logger.exception("Unexpected error processing download for job %s: %s", job.job_id, exc)
            await app.bot.send_message(
                chat_id=job.chat_id,
                text=get_user_error("telegram_upload_error"),
            )
        finally:
            # Non-negotiable invariant: clean partial files on every exit path
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except OSError:
                    pass

    return process_download


# -----------------------------------------------------------------------------
# Handlers
# -----------------------------------------------------------------------------

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    user = update.effective_user
    if not is_authorized(user.id if user else None, config):
        await reject_unauthorized(update)
        return

    text = (
        "📚 *Welcome to Nabu*\n\n"
        "Send me a book title or author to search the catalog.\n\n"
        "*Commands:*\n"
        "/queue — View active and pending downloads\n"
        "/status — Check bot and node operational health\n"
        "/help — Guidance on search and usage"
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
        "Tap any result to queue a download directly from local IPFS.\n"
        "If results are fewer than 5, tap *More results* to search shadow libraries."
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
    user = update.effective_user
    if not is_authorized(user.id if user else None, config):
        await reject_unauthorized(update)
        return

    kubo_online = await kubo.is_online()
    kubo_status = "OK" if kubo_online else "OFFLINE"
    stats = queue.stats()

    text = (
        "🤖 *System Status*\n"
        "Bot: online\n"
        "Database: OK\n"
        f"Kubo: {kubo_status}\n"
        f"Active downloads: {stats['active']}/{queue.max_workers}\n"
        f"Queue: {stats['queued']}"
    )
    if update.effective_message:
        await update.effective_message.reply_text(text, parse_mode="Markdown")


async def search_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    user = update.effective_user
    if not is_authorized(user.id if user else None, config):
        await reject_unauthorized(update)
        return

    query_text = (update.effective_message.text or "").strip() if update.effective_message else ""
    if not query_text:
        return

    # 1. Search local SQLite cache
    hits = await db.search_books(query_text, limit=5)

    if not hits:
        # No local results found
        no_res_text = f'No books found for "{query_text}".'
        # Future step 7/8 will auto-trigger sources when AUTO_ACQUIRE=true
        if update.effective_message:
            await update.effective_message.reply_text(no_res_text)
        return

    # 2. Build inline keyboard for hits
    keyboard: list[list[InlineKeyboardButton]] = []
    for book in hits:
        author_str = f" — {book.author}" if book.author else ""
        button_text = f"{book.title}{author_str}"
        # Truncate button label if too long for mobile view
        if len(button_text) > 48:
            button_text = button_text[:45] + "…"
        cb_data = encode_callback("book", book.id)
        keyboard.append([InlineKeyboardButton(button_text, callback_data=cb_data)])

    # If local hits < 5, append "More results" button
    if len(hits) < 5:
        # Cache query for more results button
        token = candidates.store(user.id if user else 0, [])
        cb_more = encode_callback("more", token)
        keyboard.append(
            [InlineKeyboardButton("🌐 More results from Anna's Archive and Libgen", callback_data=cb_more)]
        )

    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.effective_message:
        await update.effective_message.reply_text("Search results:", reply_markup=reply_markup)


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    # Non-negotiable: answer callback query immediately
    await query.answer()

    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    queue: QueueManager = context.bot_data["queue"]
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
            if query.message:
                await query.message.reply_text(get_user_error("missing_cid"))
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

    elif action == "more":
        if query.message:
            await query.message.reply_text("🔎 External source search will be available in Step 7.")


# -----------------------------------------------------------------------------
# Bot Application Builder & Lifecycle
# -----------------------------------------------------------------------------

def build_application(
    config: Config,
    db: Database | None = None,
    kubo: KuboClient | None = None,
    queue: QueueManager | None = None,
) -> Application:
    """Build and configure the Telegram Application instance."""
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

    # Store shared objects in bot_data
    app.bot_data["config"] = config
    app.bot_data["db"] = active_db
    app.bot_data["kubo"] = active_kubo
    app.bot_data["queue"] = active_queue

    # Register handlers
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("queue", queue_handler))
    app.add_handler(CommandHandler("status", status_handler))
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
        logger.info("Bot startup complete")

    async def post_shutdown(application: Application) -> None:
        logger.info("Shutting down bot...")
        await active_queue.stop()
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
