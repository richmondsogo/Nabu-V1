"""Unit tests for AcquisitionManager and the acquisition pipeline."""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

from acquirer import AcquisitionManager
from database import Database
from ipfs import KuboClient
from models import DownloadHandle, DownloadJob, SearchHit
from queue_manager import QueueManager
from sources.base import BaseSource
from sources.resolver import SourceResolver


class MockSource(BaseSource):
    """Mock source adapter for testing acquirer."""

    name = "mock_src"

    def __init__(
        self,
        hits: list[SearchHit] | None = None,
        handle: DownloadHandle | None = None,
        download_payload: bytes = b"%PDF-1.4 mock valid pdf document",
        delay: float = 0.0,
    ) -> None:
        super().__init__(mirrors=("https://mock.example.com",))
        self.hits = hits or []
        self.handle = handle
        self.download_payload = download_payload
        self.delay = delay
        self.download_calls = 0

    async def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.hits[:limit]

    async def resolve(self, hit: SearchHit) -> DownloadHandle:
        if self.handle:
            return self.handle
        return DownloadHandle(
            kind="url",
            url=f"https://mock.example.com/download/{hit.source_id}",
            source=self.name,
            md5=hit.md5,
        )

    async def download(
        self,
        handle: DownloadHandle,
        dest: Path,
        max_bytes: int,
        timeout: float,
    ) -> int:
        self.download_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.download_payload)
        return len(self.download_payload)


class MockKuboClient(KuboClient):
    """Mock Kubo RPC client."""

    def __init__(
        self,
        cat_payload: bytes = b"%PDF-1.4 mock ipfs cat content",
        cat_delay: float = 0.0,
    ) -> None:
        super().__init__(api_url="http://127.0.0.1:5001")
        self.cat_payload = cat_payload
        self.cat_delay = cat_delay
        self.pinned_cids: list[str] = []
        self.added_files: list[Path] = []

    async def cat_file(
        self,
        cid: str,
        dest: Path,
        max_bytes: int,
        timeout: float,
    ) -> int:
        if self.cat_delay:
            await asyncio.sleep(self.cat_delay)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.cat_payload)
        return len(self.cat_payload)

    async def pin_cid(self, cid: str, timeout: float = 30.0) -> bool:
        self.pinned_cids.append(cid)
        return True

    async def add_file(self, file_path: Path, pin: bool = True) -> str:
        self.added_files.append(file_path)
        return "bafykbzaced_mock_added_cid_123"


@pytest.fixture
def test_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test_acq.db")
    db.init_schema()
    return db


@pytest.mark.asyncio
async def test_acquirer_url_download_route_success(test_db: Database, tmp_path: Path) -> None:
    hit = SearchHit(
        source="mock_src",
        source_id="m-1",
        title="Domain Driven Design",
        author="Eric Evans",
        md5="1234567890abcdef1234567890abcdef",
    )
    src = MockSource(hits=[hit])
    resolver = SourceResolver(sources={"mock_src": src}, priority=("mock_src",))
    kubo = MockKuboClient()
    delivered_jobs: list[DownloadJob] = []

    async def mock_delivery_handler(j: DownloadJob) -> None:
        delivered_jobs.append(j)

    delivery_queue = QueueManager(handler=mock_delivery_handler, max_workers=1)
    delivery_queue.start()

    manager = AcquisitionManager(
        db=test_db,
        kubo_client=kubo,
        resolver=resolver,
        delivery_queue=delivery_queue,
        temp_dir=tmp_path / "tmp",
    )
    await manager.start()

    status_updates = []

    async def on_status(msg: str) -> None:
        status_updates.append(msg)

    job = await manager.enqueue(
        query="Domain Driven Design",
        user_id=100,
        chat_id=100,
        hit=hit,
        status_callback=on_status,
    )

    # Wait for acquisition worker to complete
    for _ in range(50):
        if manager.completed_count >= 1:
            break
        await asyncio.sleep(0.05)

    assert manager.completed_count == 1
    assert job.status == "imported"
    assert job.book_id is not None

    # Verify book in database
    book = await test_db.get_book_by_id(job.book_id)
    assert book is not None
    assert book.title == "Domain Driven Design"
    assert book.cid == "bafykbzaced_mock_added_cid_123"
    assert book.pinned is True

    # Verify delivery queue executed delivery for user
    for _ in range(50):
        if len(delivered_jobs) >= 1:
            break
        await asyncio.sleep(0.05)
    assert len(delivered_jobs) == 1
    assert delivered_jobs[0].book_id == job.book_id
    assert delivered_jobs[0].user_id == 100

    # Verify temp file is cleaned up
    temp_file = tmp_path / "tmp" / f"{job.job_id}.part"
    assert not temp_file.exists()

    await manager.stop()
    await delivery_queue.stop()


@pytest.mark.asyncio
async def test_acquirer_cid_first_route_success(test_db: Database, tmp_path: Path) -> None:
    hit = SearchHit(
        source="mock_src",
        source_id="m-2",
        title="Clean Architecture",
        author="Robert C. Martin",
        cid="bafykbzaced_cid_clean_arch",
        md5="abcdef1234567890abcdef1234567890",
    )
    cid_handle = DownloadHandle(kind="cid", cid="bafykbzaced_cid_clean_arch", source="mock_src")
    src = MockSource(hits=[hit], handle=cid_handle)
    resolver = SourceResolver(sources={"mock_src": src})
    kubo = MockKuboClient()

    manager = AcquisitionManager(
        db=test_db,
        kubo_client=kubo,
        resolver=resolver,
        temp_dir=tmp_path / "tmp",
    )
    await manager.start()

    job = await manager.enqueue(
        query="Clean Architecture",
        user_id=101,
        chat_id=101,
        hit=hit,
    )

    for _ in range(50):
        if manager.completed_count >= 1:
            break
        await asyncio.sleep(0.05)

    assert manager.completed_count == 1
    assert "bafykbzaced_cid_clean_arch" in kubo.pinned_cids
    assert src.download_calls == 0  # Bypassed HTTP download entirely!

    book = await test_db.get_book_by_id(job.book_id)
    assert book is not None
    assert book.cid == "bafykbzaced_cid_clean_arch"

    await manager.stop()


@pytest.mark.asyncio
async def test_acquirer_one_active_per_user_gate(test_db: Database, tmp_path: Path) -> None:
    src = MockSource(delay=0.1)
    resolver = SourceResolver(sources={"mock_src": src})
    kubo = MockKuboClient()

    manager = AcquisitionManager(
        db=test_db,
        kubo_client=kubo,
        resolver=resolver,
        temp_dir=tmp_path / "tmp",
    )
    await manager.start()

    hit1 = SearchHit(source="mock_src", source_id="1", title="Book 1")
    hit2 = SearchHit(source="mock_src", source_id="2", title="Book 2")

    # User 200 enqueues two books in a row
    job1 = await manager.enqueue(query="Book 1", user_id=200, chat_id=200, hit=hit1)
    job2 = await manager.enqueue(query="Book 2", user_id=200, chat_id=200, hit=hit2)

    # Job 1 is active, Job 2 must be in waiting queue
    assert 200 in manager._user_active
    assert manager._user_active[200] == job1.job_id
    assert 200 in manager._user_waiting
    assert len(manager._user_waiting[200]) == 1
    assert manager._user_waiting[200][0].job_id == job2.job_id

    # Wait for both jobs to complete sequentially
    for _ in range(100):
        if manager.completed_count >= 2:
            break
        await asyncio.sleep(0.05)

    assert manager.completed_count == 2
    assert 200 not in manager._user_active
    assert 200 not in manager._user_waiting

    await manager.stop()


@pytest.mark.asyncio
async def test_acquirer_global_md5_deduplication(test_db: Database, tmp_path: Path) -> None:
    shared_md5 = "deadbeefdeadbeefdeadbeefdeadbeef"
    hit = SearchHit(source="mock_src", source_id="1", title="Shared Book", md5=shared_md5)
    src = MockSource(hits=[hit], delay=0.1)
    delivered_jobs: list[DownloadJob] = []

    async def mock_delivery_handler(j: DownloadJob) -> None:
        delivered_jobs.append(j)

    delivery_queue = QueueManager(handler=mock_delivery_handler, max_workers=1)
    delivery_queue.start()
    resolver = SourceResolver(sources={"mock_src": src})
    kubo = MockKuboClient()

    manager = AcquisitionManager(
        db=test_db,
        kubo_client=kubo,
        resolver=resolver,
        delivery_queue=delivery_queue,
        temp_dir=tmp_path / "tmp",
    )
    await manager.start()

    # User 301 and User 302 request the exact same missing book concurrently
    job1 = await manager.enqueue(query="Shared Book", user_id=301, chat_id=301, hit=hit, md5=shared_md5)
    job2 = await manager.enqueue(query="Shared Book", user_id=302, chat_id=302, hit=hit, md5=shared_md5)

    # Only 1 job in queue
    assert manager._queue.qsize() == 0  # (job1 was pulled immediately by worker, job2 was deduplicated)
    assert len(manager._active_by_md5[shared_md5]) == 2

    for _ in range(50):
        if manager.completed_count >= 1:
            break
        await asyncio.sleep(0.05)

    assert manager.completed_count == 1
    assert src.download_calls == 1  # Exactly ONE network download occurred!

    # Wait for delivery queue to process deliveries
    for _ in range(50):
        if len(delivered_jobs) >= 2:
            break
        await asyncio.sleep(0.05)

    # Both users got delivered!
    assert len(delivered_jobs) == 2
    user_ids = {j.user_id for j in delivered_jobs}
    assert user_ids == {301, 302}

    await manager.stop()
    await delivery_queue.stop()


@pytest.mark.asyncio
async def test_acquirer_corrupt_file_rejected_and_cleaned_up(test_db: Database, tmp_path: Path) -> None:
    # Source returns HTML error page instead of valid book
    html_error = b"<html><body>404 Cloudflare Not Found</body></html>"
    src = MockSource(download_payload=html_error)
    resolver = SourceResolver(sources={"mock_src": src})
    kubo = MockKuboClient()

    manager = AcquisitionManager(
        db=test_db,
        kubo_client=kubo,
        resolver=resolver,
        temp_dir=tmp_path / "tmp",
    )
    await manager.start()

    hit = SearchHit(source="mock_src", source_id="bad-1", title="Corrupt Book")
    job = await manager.enqueue(query="Corrupt Book", user_id=400, chat_id=400, hit=hit)

    for _ in range(50):
        if manager.failed_count >= 1:
            break
        await asyncio.sleep(0.05)

    assert manager.failed_count == 1
    assert job.status == "failed"
    assert "failed validation" in (job.error or "")

    # Zero books in database
    books = await test_db.search_books("Corrupt Book")
    assert len(books) == 0

    # Temp file must not exist
    part_file = tmp_path / "tmp" / f"{job.job_id}.part"
    assert not part_file.exists()

    await manager.stop()


@pytest.mark.asyncio
async def test_acquirer_timeout_aborts_and_cleans_up(test_db: Database, tmp_path: Path) -> None:
    # Source with large delay exceeding acquire_timeout
    src = MockSource(delay=1.0)
    resolver = SourceResolver(sources={"mock_src": src})
    kubo = MockKuboClient()

    manager = AcquisitionManager(
        db=test_db,
        kubo_client=kubo,
        resolver=resolver,
        acquire_timeout=0.1,  # Short timeout for testing
        temp_dir=tmp_path / "tmp",
    )
    await manager.start()

    hit = SearchHit(source="mock_src", source_id="slow-1", title="Slow Book")
    job = await manager.enqueue(query="Slow Book", user_id=500, chat_id=500, hit=hit)

    for _ in range(50):
        if manager.failed_count >= 1:
            break
        await asyncio.sleep(0.05)

    assert manager.failed_count == 1
    assert job.status == "failed"
    assert "TimeoutError" in (job.error or "") or "timed out" in (job.error or "").lower() or job.error == ""

    part_file = tmp_path / "tmp" / f"{job.job_id}.part"
    assert not part_file.exists()

    await manager.stop()


@pytest.mark.asyncio
async def test_acquirer_auto_search_fallback_when_no_hit(test_db: Database, tmp_path: Path) -> None:
    hit = SearchHit(
        source="mock_src",
        source_id="found-1",
        title="Automated Search Book",
        md5="aabbccddeeff00112233445566778899",
    )
    src = MockSource(hits=[hit])
    resolver = SourceResolver(sources={"mock_src": src}, priority=("mock_src",))
    kubo = MockKuboClient()

    manager = AcquisitionManager(
        db=test_db,
        kubo_client=kubo,
        resolver=resolver,
        temp_dir=tmp_path / "tmp",
    )
    await manager.start()

    # Enqueue with only query text and no pre-selected hit
    job = await manager.enqueue(query="Automated Search Book", user_id=600, chat_id=600)

    for _ in range(50):
        if manager.completed_count >= 1:
            break
        await asyncio.sleep(0.05)

    assert manager.completed_count == 1
    assert job.status == "imported"
    assert job.hit is not None
    assert job.hit.title == "Automated Search Book"

    # Verify book was inserted in database
    book = await test_db.get_book_by_id(job.book_id)
    assert book is not None
    assert book.title == "Automated Search Book"

    await manager.stop()


@pytest.mark.asyncio
async def test_acquirer_updates_existing_book_cid_without_duplicate(test_db: Database, tmp_path: Path) -> None:
    # Book already known in database but with null CID
    existing_book = await test_db.insert_book(
        title="Existing Pre-seeded Book",
        author="Original Author",
        cid=None,
        md5="55555555555555555555555555555555",
    )
    assert existing_book.cid is None

    hit = SearchHit(
        source="mock_src",
        source_id="exist-1",
        title="Existing Pre-seeded Book",
        md5="55555555555555555555555555555555",
    )
    src = MockSource(hits=[hit])
    resolver = SourceResolver(sources={"mock_src": src})
    kubo = MockKuboClient()

    manager = AcquisitionManager(
        db=test_db,
        kubo_client=kubo,
        resolver=resolver,
        temp_dir=tmp_path / "tmp",
    )
    await manager.start()

    job = await manager.enqueue(query="Existing Pre-seeded Book", user_id=700, chat_id=700, hit=hit)

    for _ in range(50):
        if manager.completed_count >= 1:
            break
        await asyncio.sleep(0.05)

    assert manager.completed_count == 1
    assert job.book_id == existing_book.id  # Updated the exact same row!

    updated = await test_db.get_book_by_id(existing_book.id)
    assert updated is not None
    assert updated.cid == "bafykbzaced_mock_added_cid_123"
    assert updated.pinned is True

    # Confirm no duplicate row created
    results = await test_db.search_books("Existing Pre-seeded Book")
    assert len(results) == 1

    await manager.stop()
