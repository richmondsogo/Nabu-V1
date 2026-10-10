"""Unit and integration tests for bot.py."""

import asyncio
from pathlib import Path
import time
from unittest.mock import AsyncMock, MagicMock
import pytest

from bot import (
    build_application,
    handle_callback,
    handle_help,
    handle_mirrors,
    handle_rebuild,
    handle_start,
    handle_status,
    handle_text_search,
    is_authorized,
)
from config import Config
from database import Database
from models import Book, SearchOutcome
from search import SearchService


@pytest.fixture
def mock_config(tmp_path: Path) -> Config:
    return Config(
        telegram_token="123456:FAKE_TELEGRAM_TOKEN",
        allowed_user_ids=frozenset({1001, 1002}),
        db_path=tmp_path / "bot_test.db",
        refresh_cooldown=60.0,
    )


@pytest.fixture
def mock_db(mock_config: Config) -> Database:
    db = Database(mock_config.db_path)
    db.init_schema()
    return db


def test_authorization_check(mock_config: Config):
    assert is_authorized(1001, mock_config) is True
    assert is_authorized(1002, mock_config) is True
    assert is_authorized(9999, mock_config) is False
    assert is_authorized(None, mock_config) is False


@pytest.mark.asyncio
async def test_unauthorized_user_rejected(mock_config: Config, mock_db: Database):
    update = MagicMock()
    update.effective_user.id = 9999  # Not authorized
    update.effective_message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"config": mock_config, "db": mock_db}

    await handle_start(update, context)
    update.effective_message.reply_text.assert_called_once_with("Sorry, this bot is private.")


@pytest.mark.asyncio
async def test_start_and_help_handlers(mock_config: Config, mock_db: Database):
    update = MagicMock()
    update.effective_user.id = 1001
    update.effective_message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"config": mock_config, "db": mock_db}

    await handle_start(update, context)
    update.effective_message.reply_text.assert_called_once()
    msg = update.effective_message.reply_text.call_args[0][0]
    assert "Welcome to Nabu" in msg

    update.effective_message.reply_text.reset_mock()
    await handle_help(update, context)
    update.effective_message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_status_and_mirrors_and_rebuild_handlers(mock_config: Config, mock_db: Database):
    update = MagicMock()
    update.effective_user.id = 1001
    update.effective_message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"config": mock_config, "db": mock_db}

    # /status
    await handle_status(update, context)
    update.effective_message.reply_text.assert_called_once()
    status_msg = update.effective_message.reply_text.call_args[0][0]
    assert "Nabu Status" in status_msg

    # /mirrors
    update.effective_message.reply_text.reset_mock()
    await handle_mirrors(update, context)
    update.effective_message.reply_text.assert_called_once()
    mirrors_msg = update.effective_message.reply_text.call_args[0][0]
    assert "Configured Mirrors" in mirrors_msg
    assert "libgen.li" in mirrors_msg

    # /rebuild
    update.effective_message.reply_text.reset_mock()
    await handle_rebuild(update, context)
    update.effective_message.reply_text.assert_called_once_with("✅ Search index rebuilt successfully.")


@pytest.mark.asyncio
async def test_search_results_flow(mock_config: Config, mock_db: Database):
    update = MagicMock()
    update.effective_user.id = 1001
    update.effective_message.text = "clean architecture"
    update.effective_message.reply_text = AsyncMock()

    search_service = MagicMock(spec=SearchService)
    book = Book(id=42, title="Clean Architecture", author="Robert C. Martin", md5="clean123", file_type="pdf", file_size=1048576)
    search_service.search_books = AsyncMock(return_value=SearchOutcome(hits=[book], source="upstream", degraded=False))

    context = MagicMock()
    context.bot_data = {"config": mock_config, "db": mock_db, "search_service": search_service}

    await handle_text_search(update, context)

    update.effective_message.reply_text.assert_called_once()
    args, kwargs = update.effective_message.reply_text.call_args
    assert "Found 1 result(s):" in args[0]
    reply_markup = kwargs["reply_markup"]
    assert len(reply_markup.inline_keyboard) == 3  # 1 book button + 1 filter row + 1 refresh button
    assert reply_markup.inline_keyboard[0][0].callback_data == "book:42"
    assert "filter:" in reply_markup.inline_keyboard[1][0].callback_data
    assert "refresh:" in reply_markup.inline_keyboard[2][0].callback_data


@pytest.mark.asyncio
async def test_callback_builds_links_with_zero_network(mock_config: Config, mock_db: Database):
    book = await mock_db.insert_book(
        title="The Rust Programming Language",
        author="Steve Klabnik",
        md5="7a7ef891b9d2b2ae8d9cd864556f7cd8",
        file_size=283648,
        file_type="epub",
    )

    update = MagicMock()
    query = update.callback_query
    query.from_user.id = 1001
    query.data = f"book:{book.id}"
    query.answer = AsyncMock()
    query.message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"config": mock_config, "db": mock_db, "search_service": MagicMock()}

    await handle_callback(update, context)

    # 1. Answer callback immediately
    query.answer.assert_called_once()

    # 2. Reply text containing formatted links
    query.message.reply_text.assert_called_once()
    msg_html = query.message.reply_text.call_args[0][0]
    assert "The Rust Programming Language" in msg_html
    assert "Steve Klabnik" in msg_html
    assert "EPUB" in msg_html
    assert "https://libgen.li/ads.php?md5=7a7ef891b9d2b2ae8d9cd864556f7cd8" in msg_html
    assert "https://libgen.la/ads.php?md5=7a7ef891b9d2b2ae8d9cd864556f7cd8" in msg_html
    assert "https://libgen.is/book/index.php?md5=7a7ef891b9d2b2ae8d9cd864556f7cd8" in msg_html


@pytest.mark.asyncio
async def test_callback_hit_with_no_md5_renders_without_links(mock_config: Config, mock_db: Database):
    book = await mock_db.insert_book(
        title="Book Without Any Hash",
        author="Mystery Author",
        md5=None,
        file_size=100000,
        file_type="pdf",
    )

    update = MagicMock()
    query = update.callback_query
    query.from_user.id = 1001
    query.data = f"book:{book.id}"
    query.answer = AsyncMock()
    query.message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"config": mock_config, "db": mock_db, "search_service": MagicMock()}

    await handle_callback(update, context)

    query.answer.assert_called_once()
    query.message.reply_text.assert_called_once()
    msg_html = query.message.reply_text.call_args[0][0]
    assert "Book Without Any Hash" in msg_html
    assert "(no direct link)" in msg_html
    assert "ads.php" not in msg_html


@pytest.mark.asyncio
async def test_post_init_launches_startup_probe_and_maintenance():
    from bot import post_init

    mock_app = MagicMock()
    mock_mm = MagicMock()
    fake_task = asyncio.create_task(asyncio.sleep(0.01))
    mock_mm.startup_probe.return_value = fake_task
    mock_db = MagicMock()
    mock_config = MagicMock()
    mock_config.maintenance_interval_sec = 100.0

    mock_app.bot_data = {
        "mirror_manager": mock_mm,
        "db": mock_db,
        "config": mock_config,
    }

    await post_init(mock_app)
    assert mock_app.bot_data["startup_probe_task"] is fake_task
    assert "maintenance_task" in mock_app.bot_data
    mock_app.bot_data["maintenance_task"].cancel()
    try:
        await mock_app.bot_data["maintenance_task"]
    except asyncio.CancelledError:
        pass
    mock_mm.startup_probe.assert_called_once()
    await fake_task


@pytest.mark.asyncio
async def test_post_shutdown_cancels_background_tasks():
    from bot import post_shutdown

    mock_app = MagicMock()
    mock_task = asyncio.create_task(asyncio.sleep(10.0))
    mock_m_task = asyncio.create_task(asyncio.sleep(10.0))
    mock_search = MagicMock()
    mock_search.aclose = AsyncMock()
    mock_mm = MagicMock()
    mock_mm.aclose = AsyncMock()
    mock_db = MagicMock()
    mock_db.wal_checkpoint = AsyncMock()

    mock_app.bot_data = {
        "startup_probe_task": mock_task,
        "maintenance_task": mock_m_task,
        "search_service": mock_search,
        "mirror_manager": mock_mm,
        "db": mock_db,
    }

    await post_shutdown(mock_app)
    assert mock_task.cancelled() or mock_task.done()
    assert mock_m_task.cancelled() or mock_m_task.done()
    mock_search.aclose.assert_awaited_once()
    mock_mm.aclose.assert_awaited_once()
    mock_db.wal_checkpoint.assert_awaited_once_with("TRUNCATE")


@pytest.mark.asyncio
async def test_maintenance_loop_and_uptime():
    from bot import _format_uptime, _maintenance_loop

    assert "1h" in _format_uptime(time.time() - 3661)
    assert "30s" in _format_uptime(time.time() - 30)

    mock_db = MagicMock()
    mock_db.prune_expired_cache = AsyncMock(return_value=2)
    mock_db.wal_checkpoint = AsyncMock(return_value={"busy": 0})

    task = asyncio.create_task(_maintenance_loop(mock_db, interval_sec=0.01))
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert mock_db.prune_expired_cache.call_count >= 1
    assert mock_db.wal_checkpoint.call_count >= 1


@pytest.mark.asyncio
async def test_callback_unauthorized_user_rejected(mock_config: Config, mock_db: Database):
    update = MagicMock()
    query = update.callback_query
    query.from_user.id = 9999  # Unauthorized
    query.data = "book:1"
    query.answer = AsyncMock()

    context = MagicMock()
    context.bot_data = {"config": mock_config, "db": mock_db}

    await handle_callback(update, context)
    query.answer.assert_called_once_with("Sorry, this bot is private.", show_alert=True)


@pytest.mark.asyncio
async def test_callback_pagination_page_switch(mock_config: Config, mock_db: Database):
    from bot import _qhash, _query_registry
    from utils import encode_callback

    # Insert 10 books to ensure multiple pages (page_size default is 8)
    books = []
    for i in range(10):
        b = await mock_db.insert_book(title=f"Test Book {i}", author="Author", md5=f"hash{i}")
        books.append(b)

    query_str = "test book"
    qh = _qhash(query_str)
    _query_registry[qh] = (query_str, 0.0)

    # Cache the book IDs
    await mock_db.set_search_cache(query_str, [b.id for b in books], ttl=3600.0)

    update = MagicMock()
    query = update.callback_query
    query.from_user.id = 1001
    query.data = encode_callback("page", qh, 2)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"config": mock_config, "db": mock_db, "search_service": MagicMock()}

    await handle_callback(update, context)

    query.answer.assert_called_once()
    query.edit_message_text.assert_called_once()
    msg_html = query.edit_message_text.call_args[0][0]
    # Page 2 should display items 9-10
    assert "9-10 of 10" in msg_html or "Page 2" in msg_html or "Test Book 8" in msg_html


@pytest.mark.asyncio
async def test_callback_resolves_direct_download_link(mock_config: Config, mock_db: Database):
    book = await mock_db.insert_book(
        title="Direct Download Book",
        author="Author Name",
        md5="1234567890abcdef1234567890abcdef",
        file_size=1024,
        file_type="pdf",
    )

    update = MagicMock()
    query = update.callback_query
    query.from_user.id = 1001
    query.data = f"book:{book.id}"
    query.answer = AsyncMock()
    query.message.reply_text = AsyncMock()

    mock_mirror_mgr = MagicMock()
    mock_mirror_mgr.resolve_direct_link = AsyncMock(
        return_value="https://libgen.li/get.php?md5=1234567890abcdef1234567890abcdef&key=KEY12345"
    )

    context = MagicMock()
    context.bot_data = {
        "config": mock_config,
        "db": mock_db,
        "mirror_manager": mock_mirror_mgr,
        "search_service": MagicMock(),
    }

    await handle_callback(update, context)

    query.message.reply_text.assert_called_once()
    msg_html = query.message.reply_text.call_args[0][0]
    assert "https://libgen.li/get.php?md5=1234567890abcdef1234567890abcdef&key=KEY12345" in msg_html
    assert "Link is temporary. Tap the book again for a fresh one." in msg_html
    assert "Direct Download" in msg_html


@pytest.mark.asyncio
async def test_faq_and_help_faq_content(mock_config: Config, mock_db: Database):
    update = MagicMock()
    update.effective_user.id = 1001
    update.effective_message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"config": mock_config, "db": mock_db}

    await handle_help(update, context)
    update.effective_message.reply_text.assert_called_once()
    msg = update.effective_message.reply_text.call_args[0][0]
    assert "Frequently Asked Questions (FAQ)" in msg
    assert "Direct download keys are temporary" in msg
    assert "simply tap the book card again in Telegram" in msg


def test_httpx_logging_silenced():
    import logging
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


@pytest.mark.asyncio
async def test_error_handler_notifies_user_on_message():
    from bot import error_handler
    from telegram import Update

    update = MagicMock(spec=Update)
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None

    context = MagicMock()
    context.error = RuntimeError("Simulated crash")

    await error_handler(update, context)
    update.effective_message.reply_text.assert_called_once_with(
        "⚠️ An unexpected error occurred. Please try again."
    )


@pytest.mark.asyncio
async def test_error_handler_notifies_user_on_callback():
    from bot import error_handler
    from telegram import Update

    update = MagicMock(spec=Update)
    update.effective_message = None
    update.callback_query.answer = AsyncMock()

    context = MagicMock()
    context.error = RuntimeError("Callback error")

    await error_handler(update, context)
    update.callback_query.answer.assert_called_once_with(
        "⚠️ An error occurred. Please try again.", show_alert=True
    )


@pytest.mark.asyncio
async def test_callback_malformed_arguments_handled_gracefully(mock_config: Config, mock_db: Database):
    update = MagicMock()
    query = update.callback_query
    query.from_user.id = 1001
    query.answer = AsyncMock()
    query.message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"config": mock_config, "db": mock_db, "search_service": MagicMock()}

    # Non-integer book id
    query.data = "book:notanumber"
    await handle_callback(update, context)
    query.answer.assert_called_once()
    query.message.reply_text.assert_not_called()

    # Non-integer page id
    query.answer.reset_mock()
    query.data = "page:dummyqh:notanumber"
    await handle_callback(update, context)
    query.answer.assert_called_once()


@pytest.mark.asyncio
async def test_callback_format_filter(mock_config: Config, mock_db: Database):
    b_epub = await mock_db.insert_book(
        title="Epub Guide",
        author="Author One",
        md5="epub123",
        file_size=1024,
        file_type="epub",
    )
    b_pdf = await mock_db.insert_book(
        title="PDF Manual",
        author="Author Two",
        md5="pdf123",
        file_size=2048,
        file_type="pdf",
    )

    from bot import _qhash, _query_registry
    raw_query = "guide manual"
    qh = _qhash(raw_query)
    _query_registry[qh] = (raw_query, 0.0)

    from search import normalize_query
    qn = normalize_query(raw_query)
    await mock_db.set_search_cache(qn, [b_epub.id, b_pdf.id], ttl=3600)

    update = MagicMock()
    query = update.callback_query
    query.from_user.id = 1001
    query.data = f"filter:{qh}:epub"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"config": mock_config, "db": mock_db, "search_service": MagicMock()}

    await handle_callback(update, context)

    query.answer.assert_called_once()
    query.edit_message_text.assert_called_once()
    text = query.edit_message_text.call_args[0][0]
    assert "[EPUB]" in text
    assert "Epub Guide" in text
    assert "PDF Manual" not in text

    # Switch back to all
    query.edit_message_text.reset_mock()
    query.data = f"filter:{qh}:all"
    await handle_callback(update, context)
    text_all = query.edit_message_text.call_args[0][0]
    assert "Epub Guide" in text_all
    assert "PDF Manual" in text_all


@pytest.mark.asyncio
async def test_handle_inline_query_success(mock_config: Config, mock_db: Database):
    update = MagicMock()
    inline_q = update.inline_query
    inline_q.from_user.id = 1001
    inline_q.query = "python programming"
    inline_q.answer = AsyncMock()

    book = Book(id=99, title="Fluent Python", author="Luciano Ramalho", md5="fluent123", file_type="epub", file_size=500000)
    search_service = MagicMock(spec=SearchService)
    search_service.search_books = AsyncMock(return_value=SearchOutcome(hits=[book], source="upstream"))

    context = MagicMock()
    context.bot_data = {"config": mock_config, "db": mock_db, "search_service": search_service}

    from bot import handle_inline_query
    await handle_inline_query(update, context)

    inline_q.answer.assert_called_once()
    results = inline_q.answer.call_args[0][0]
    assert len(results) == 1
    assert results[0].title == "Fluent Python"
    assert "Luciano Ramalho" in results[0].description
    assert "[EPUB]" in results[0].description


@pytest.mark.asyncio
async def test_handle_inline_query_unauthorized(mock_config: Config, mock_db: Database):
    update = MagicMock()
    inline_q = update.inline_query
    inline_q.from_user.id = 9999  # unauthorized
    inline_q.query = "python"
    inline_q.answer = AsyncMock()

    context = MagicMock()
    context.bot_data = {"config": mock_config, "db": mock_db, "search_service": MagicMock()}

    from bot import handle_inline_query
    await handle_inline_query(update, context)

    inline_q.answer.assert_called_once_with([], is_personal=True, cache_time=5)


def test_format_icon_mapping():
    from bot import _format_icon

    assert _format_icon("epub") == "📘"
    assert _format_icon("EPUB") == "📘"
    assert _format_icon("pdf") == "📕"
    assert _format_icon("mobi") == "📙"
    assert _format_icon("azw3") == "📙"
    assert _format_icon("djvu") == "📗"
    assert _format_icon("txt") == "📄"
    assert _format_icon(None) == "📄"


def test_format_book_card_keyboard():
    from bot import _format_book_card_keyboard

    book_with_md5 = Book(id=1, title="Test", md5="0123456789abcdef0123456789abcdef")
    book_without_md5 = Book(id=2, title="No Hash", md5=None)

    # When no MD5, keyboard is None
    assert _format_book_card_keyboard(book_without_md5) is None

    # With direct download URL
    kb_direct = _format_book_card_keyboard(book_with_md5, direct_url="https://example.com/get.php")
    assert kb_direct is not None
    assert len(kb_direct.inline_keyboard) == 2
    assert kb_direct.inline_keyboard[0][0].text == "⚡ Instant Download (One-Click)"
    assert kb_direct.inline_keyboard[0][0].url == "https://example.com/get.php"
    assert "libgen.li" in kb_direct.inline_keyboard[1][0].url
    assert "libgen.is" in kb_direct.inline_keyboard[1][1].url

    # Fallback without direct download URL
    kb_fallback = _format_book_card_keyboard(book_with_md5, direct_url=None)
    assert kb_fallback is not None
    assert len(kb_fallback.inline_keyboard) == 2
    assert "Libgen.li" in kb_fallback.inline_keyboard[0][0].text
    assert "Anna's Archive" in kb_fallback.inline_keyboard[1][1].text




