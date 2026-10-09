"""Unit and integration tests for bot.py."""

import asyncio
from pathlib import Path
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
    assert len(reply_markup.inline_keyboard) == 2  # 1 book button + 1 refresh button
    assert reply_markup.inline_keyboard[0][0].callback_data == "book:42"
    assert "refresh:" in reply_markup.inline_keyboard[1][0].callback_data


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
async def test_post_init_launches_startup_probe():
    from bot import post_init

    mock_app = MagicMock()
    mock_mm = MagicMock()
    fake_task = asyncio.create_task(asyncio.sleep(0.01))
    mock_mm.startup_probe.return_value = fake_task
    mock_app.bot_data = {"mirror_manager": mock_mm}

    await post_init(mock_app)
    assert mock_app.bot_data["startup_probe_task"] is fake_task
    mock_mm.startup_probe.assert_called_once()
    await fake_task


@pytest.mark.asyncio
async def test_post_shutdown_cancels_background_tasks():
    from bot import post_shutdown

    mock_app = MagicMock()
    mock_task = asyncio.create_task(asyncio.sleep(10.0))
    mock_app.bot_data = {"startup_probe_task": mock_task}

    await post_shutdown(mock_app)
    assert mock_task.cancelled() or mock_task.done()

