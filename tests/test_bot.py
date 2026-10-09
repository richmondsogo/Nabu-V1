"""End-to-end and unit tests for bot.py."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest
import httpx

from bot import (
    acquire_handler,
    callback_query_handler,
    create_download_processor,
    fetch_handler,
    get_handler,
    help_handler,
    is_authorized,
    queue_handler,
    rebuild_handler,
    search_message_handler,
    sources_handler,
    start_handler,
    status_handler,
)
from config import Config
from database import Database
from ipfs import KuboClient
from models import DownloadJob
from queue_manager import QueueManager


@pytest.fixture
def mock_config(tmp_path: Path) -> Config:
    return Config(
        telegram_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        allowed_user_ids=frozenset({1001, 1002}),
        db_path=tmp_path / "bot_test.db",
        ipfs_api_url="http://127.0.0.1:5001",
        temp_dir=tmp_path / "tmp",
        max_concurrent_downloads=3,
        download_timeout=5.0,
        max_telegram_file_size=52_428_800,
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
    update.effective_user.id = 9999  # Not whitelisted
    update.effective_message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"config": mock_config, "db": mock_db}

    await start_handler(update, context)
    update.effective_message.reply_text.assert_called_once_with("Sorry, this bot is private.")


@pytest.mark.asyncio
async def test_start_and_help_commands(mock_config: Config):
    update = MagicMock()
    update.effective_user.id = 1001  # Whitelisted
    update.effective_message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"config": mock_config}

    await start_handler(update, context)
    assert "Welcome to Nabu" in update.effective_message.reply_text.call_args[0][0]

    update.effective_message.reply_text.reset_mock()
    await help_handler(update, context)
    assert "How to Search" in update.effective_message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_status_and_queue_commands(mock_config: Config, tmp_path: Path):
    def kubo_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Version": "0.26.0"})

    transport = httpx.MockTransport(kubo_handler)
    kubo = KuboClient(transport=transport)
    queue = QueueManager(max_workers=3)

    update = MagicMock()
    update.effective_user.id = 1001
    update.effective_message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"config": mock_config, "kubo": kubo, "queue": queue}

    try:
        await status_handler(update, context)
        status_text = update.effective_message.reply_text.call_args[0][0]
        assert "Bot: online" in status_text
        assert "Kubo: OK" in status_text

        update.effective_message.reply_text.reset_mock()
        await queue_handler(update, context)
        queue_text = update.effective_message.reply_text.call_args[0][0]
        assert "Active workers: 0/3" in queue_text
        assert "Waiting in queue: 0" in queue_text
    finally:
        await kubo.close()


@pytest.mark.asyncio
async def test_search_and_callback_flow(mock_config: Config, mock_db: Database):
    # 1. Seed a book into local SQLite catalog
    book = await mock_db.insert_book(
        title="Clean Code",
        author="Robert C. Martin",
        cid="bafybeicleancodecid",
        file_type="pdf",
    )

    queue = QueueManager(max_workers=3)
    queue.start()

    context = MagicMock()
    context.bot_data = {
        "config": mock_config,
        "db": mock_db,
        "queue": queue,
    }

    try:
        # 2. User searches for "clean code"
        search_update = MagicMock()
        search_update.effective_user.id = 1001
        search_update.effective_message.text = "clean code"
        search_update.effective_message.reply_text = AsyncMock()

        await search_message_handler(search_update, context)
        search_update.effective_message.reply_text.assert_called_once()
        args, kwargs = search_update.effective_message.reply_text.call_args
        assert args[0] == "Search results:"
        markup = kwargs["reply_markup"]
        button = markup.inline_keyboard[0][0]
        assert "Clean Code" in button.text
        assert button.callback_data == f"book:{book.id}"

        # 3. User taps result button
        cb_update = MagicMock()
        cb_update.effective_user.id = 1001
        cb_update.callback_query.data = f"book:{book.id}"
        cb_update.callback_query.answer = AsyncMock()
        cb_update.callback_query.message.reply_text = AsyncMock()
        cb_update.callback_query.message.chat_id = 1001

        await callback_query_handler(cb_update, context)
        cb_update.callback_query.answer.assert_called_once()
        cb_update.callback_query.message.reply_text.assert_called_once()
        reply_text = cb_update.callback_query.message.reply_text.call_args[0][0]
        assert "Added to the download queue" in reply_text
        assert "Position: 1" in reply_text

        # Verify job exists in queue
        assert queue.stats()["queued"] + queue.stats()["active"] == 1
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_download_processor_end_to_end_delivery(mock_config: Config, mock_db: Database, tmp_path: Path):
    # 1. Hand-insert book row
    book = await mock_db.insert_book(
        title="Clean Architecture",
        author="Robert C. Martin",
        cid="bafybeicleanarchcid",
        file_type="pdf",
    )

    pdf_payload = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"

    def kubo_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["arg"] == "bafybeicleanarchcid"
        return httpx.Response(200, content=pdf_payload)

    transport = httpx.MockTransport(kubo_handler)
    kubo = KuboClient(transport=transport)

    mock_app = MagicMock()
    mock_app.bot.send_document = AsyncMock()

    processor = create_download_processor(mock_app, mock_db, kubo, mock_config)

    job = DownloadJob(
        job_id="job-test-e2e-1",
        book_id=book.id,
        user_id=1001,
        chat_id=1001,
        enqueued_at=12345.0,
        title="Clean Architecture",
    )

    try:
        await processor(job)

        # Assert delivery occurred
        mock_app.bot.send_document.assert_called_once()
        call_kwargs = mock_app.bot.send_document.call_args.kwargs
        assert call_kwargs["chat_id"] == 1001
        assert call_kwargs["filename"] == "Clean Architecture - Robert C. Martin.pdf"
        assert "Clean Architecture" in call_kwargs["caption"]

        # Assert temporary file was deleted after delivery
        part_file = mock_config.temp_dir / "job-test-e2e-1.part"
        assert not part_file.exists()
    finally:
        await kubo.close()


@pytest.mark.asyncio
async def test_download_processor_corrupted_payload_aborts(mock_config: Config, mock_db: Database):
    book = await mock_db.insert_book(
        title="Broken Book",
        cid="bafybeibadcid",
        file_type="pdf",
    )

    html_payload = b"<html><head><title>Cloudflare Error</title></head></html>"

    def kubo_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html_payload)

    transport = httpx.MockTransport(kubo_handler)
    kubo = KuboClient(transport=transport)

    mock_app = MagicMock()
    mock_app.bot.send_message = AsyncMock()
    mock_app.bot.send_document = AsyncMock()

    processor = create_download_processor(mock_app, mock_db, kubo, mock_config)

    job = DownloadJob(
        job_id="job-corrupt-1",
        book_id=book.id,
        user_id=1001,
        chat_id=1001,
        enqueued_at=12345.0,
    )

    try:
        await processor(job)

        # Document must NOT be sent
        mock_app.bot.send_document.assert_not_called()
        # User receives corrupt warning
        mock_app.bot.send_message.assert_called_once()
        msg_text = mock_app.bot.send_message.call_args.kwargs["text"]
        assert "corrupted" in msg_text

        # Fetch failure was recorded in DB
        updated_book = await mock_db.get_book_by_id(book.id)
        assert updated_book.fetch_failures == 1

        # Temp file deleted
        part_file = mock_config.temp_dir / "job-corrupt-1.part"
        assert not part_file.exists()
    finally:
        await kubo.close()


@pytest.mark.asyncio
async def test_get_handler(mock_config: Config):
    mock_acquirer = MagicMock()
    mock_acquirer.enqueue = AsyncMock()

    update = MagicMock()
    update.effective_user.id = 1001
    update.effective_chat.id = 1001
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    update.effective_message.reply_text = AsyncMock(return_value=status_msg)

    context = MagicMock()
    context.bot_data = {"config": mock_config, "acquirer": mock_acquirer}
    context.args = ["Clean", "Architecture"]

    # Valid get query
    await get_handler(update, context)
    mock_acquirer.enqueue.assert_called_once()
    assert mock_acquirer.enqueue.call_args.kwargs["query"] == "Clean Architecture"
    assert mock_acquirer.enqueue.call_args.kwargs["user_id"] == 1001

    # Empty get query prints usage
    context.args = []
    update.effective_message.reply_text.reset_mock()
    await get_handler(update, context)
    assert "Usage: `/get" in update.effective_message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_fetch_handler_with_and_without_cid(mock_config: Config, mock_db: Database):
    queue = QueueManager(max_workers=3)
    queue.start()
    mock_acquirer = MagicMock()
    mock_acquirer.enqueue = AsyncMock()

    # Book 1: Has CID
    book_with_cid = await mock_db.insert_book(
        title="Book With CID",
        cid="bafybeicidbook",
        file_type="epub",
    )
    # Book 2: No CID, has MD5
    book_no_cid = await mock_db.insert_book(
        title="Book No CID",
        md5="0123456789abcdef0123456789abcdef",
        file_type="pdf",
    )

    context = MagicMock()
    context.bot_data = {
        "config": mock_config,
        "db": mock_db,
        "queue": queue,
        "acquirer": mock_acquirer,
    }

    update = MagicMock()
    update.effective_user.id = 1001
    update.effective_chat.id = 1001
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    update.effective_message.reply_text = AsyncMock(return_value=status_msg)

    try:
        # 1. Fetch book with CID -> enqueues directly to delivery queue
        context.args = [str(book_with_cid.id)]
        await fetch_handler(update, context)
        assert queue.stats()["queued"] == 1
        mock_acquirer.enqueue.assert_not_called()

        # 2. Fetch book without CID -> delegates to acquirer
        context.args = [str(book_no_cid.id)]
        await fetch_handler(update, context)
        mock_acquirer.enqueue.assert_called_once()
        assert mock_acquirer.enqueue.call_args.kwargs["query"] == "Book No CID"
        assert mock_acquirer.enqueue.call_args.kwargs["md5"] == "0123456789abcdef0123456789abcdef"
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_sources_and_rebuild_handlers(mock_config: Config, mock_db: Database):
    context = MagicMock()
    context.bot_data = {"config": mock_config, "db": mock_db}

    update = MagicMock()
    update.effective_user.id = 1001
    update.effective_message.reply_text = AsyncMock()

    # 1. Sources handler
    await sources_handler(update, context)
    sources_text = update.effective_message.reply_text.call_args[0][0]
    assert "Shadow Library Sources" in sources_text
    assert "annas" in sources_text
    assert "libgen" in sources_text

    # 2. Rebuild handler
    update.effective_message.reply_text.reset_mock()
    await rebuild_handler(update, context)
    assert "rebuilt the FTS5 catalog" in update.effective_message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_acquire_handler(mock_config: Config):
    mock_acquirer = MagicMock()
    mock_acquirer._user_active = {1001: "job-active-99"}
    mock_acquirer._user_waiting = {1001: [MagicMock(query="Waiting Book 1")]}

    context = MagicMock()
    context.bot_data = {"config": mock_config, "acquirer": mock_acquirer}

    update = MagicMock()
    update.effective_user.id = 1001
    update.effective_message.reply_text = AsyncMock()

    await acquire_handler(update, context)
    text = update.effective_message.reply_text.call_args[0][0]
    assert "Your Acquisitions" in text
    assert "job-active-99" in text
    assert "Waiting Book 1" in text


@pytest.mark.asyncio
async def test_auto_acquire_on_search_miss(mock_config: Config, mock_db: Database):
    mock_acquirer = MagicMock()
    mock_acquirer.enqueue = AsyncMock()

    context = MagicMock()
    context.bot_data = {
        "config": mock_config,
        "db": mock_db,
        "acquirer": mock_acquirer,
    }

    update = MagicMock()
    update.effective_user.id = 1001
    update.effective_chat.id = 1001
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    update.effective_message.reply_text = AsyncMock(return_value=status_msg)
    update.effective_message.text = "Unknown Mysterious Book"

    # With auto_acquire enabled (default in Config)
    assert mock_config.auto_acquire is True
    await search_message_handler(update, context)

    # Status message was sent
    assert "Searching shadow libraries" in update.effective_message.reply_text.call_args[0][0]
    mock_acquirer.enqueue.assert_called_once()
    assert mock_acquirer.enqueue.call_args.kwargs["query"] == "Unknown Mysterious Book"


@pytest.mark.asyncio
async def test_callback_web_and_no_cid_handling(mock_config: Config, mock_db: Database):
    from utils import candidates, encode_callback

    mock_acquirer = MagicMock()
    mock_acquirer.enqueue = AsyncMock()
    queue = QueueManager(max_workers=3)

    # Insert book without CID
    book_no_cid = await mock_db.insert_book(
        title="Offline Cached Title",
        md5="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        file_type="pdf",
    )

    context = MagicMock()
    context.bot_data = {
        "config": mock_config,
        "db": mock_db,
        "queue": queue,
        "acquirer": mock_acquirer,
    }

    # 1. Tap book without CID -> triggers acquirer
    cb_update = MagicMock()
    cb_update.effective_user.id = 1001
    cb_update.callback_query.data = f"book:{book_no_cid.id}"
    cb_update.callback_query.answer = AsyncMock()
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    cb_update.callback_query.message.reply_text = AsyncMock(return_value=status_msg)
    cb_update.callback_query.message.chat_id = 1001

    await callback_query_handler(cb_update, context)
    mock_acquirer.enqueue.assert_called_once()
    assert mock_acquirer.enqueue.call_args.kwargs["query"] == "Offline Cached Title"
    assert mock_acquirer.enqueue.call_args.kwargs["md5"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    # 2. Tap web search callback
    mock_acquirer.enqueue.reset_mock()
    token = candidates.store(1001, [{"query": "Quantum Physics"}])
    cb_data = encode_callback("web", token)

    cb_web_update = MagicMock()
    cb_web_update.effective_user.id = 1001
    cb_web_update.callback_query.data = cb_data
    cb_web_update.callback_query.answer = AsyncMock()
    cb_web_update.callback_query.message.reply_text = AsyncMock(return_value=status_msg)
    cb_web_update.callback_query.message.chat_id = 1001

    await callback_query_handler(cb_web_update, context)
    mock_acquirer.enqueue.assert_called_once()
    assert mock_acquirer.enqueue.call_args.kwargs["query"] == "Quantum Physics"

