"""Tests for search.py covering cache-first, FTS fallback, SingleFlight, and degraded states."""

from pathlib import Path
import time
import pytest
import httpx

from config import Config
from concurrency import HostRateLimiter, SingleFlight, UserRateLimiter, create_global_semaphore
from database import Database
from models import Mirror
from search import AllMirrorsFailed, RateLimitedError, SearchService
from sources.mirror_manager import MirrorManager

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def test_config(tmp_path: Path) -> Config:
    return Config(
        telegram_token="123456:FAKE_TOKEN_FOR_TESTS",
        allowed_user_ids=frozenset({1001, 1002}),
        db_path=tmp_path / "search_test.db",
        local_result_threshold=3,
        search_cache_ttl=3600.0,
        result_limit=5,
        user_bucket_tokens=5.0,
        user_bucket_refill=0.5,
        connect_timeout=2.0,
        singleflight_timeout=5.0,
    )


@pytest.fixture
def search_service(test_config: Config) -> tuple[SearchService, Database, list[str]]:
    db = Database(test_config.db_path)
    db.init_schema()

    upstream_calls: list[str] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        upstream_calls.append(str(request.url))
        html_file = FIXTURES_DIR / "libgen_li_search.html"
        return httpx.Response(200, text=html_file.read_text(encoding="utf-8"))

    transport = httpx.MockTransport(mock_handler)
    client = httpx.AsyncClient(transport=transport)

    mirror_mgr = MirrorManager(db, connect_timeout=test_config.connect_timeout)
    sf = SingleFlight()
    host_limiter = HostRateLimiter(polite_delay_ms=0)
    sem = create_global_semaphore(4)
    user_limiter = UserRateLimiter(max_tokens=5.0, refill_per_sec=0.5)

    service = SearchService(
        db=db,
        config=test_config,
        mirror_manager=mirror_mgr,
        single_flight=sf,
        host_rate_limiter=host_limiter,
        global_semaphore=sem,
        user_rate_limiter=user_limiter,
        http_client=client,
    )
    return service, db, upstream_calls


@pytest.mark.asyncio
async def test_cache_hit_makes_zero_upstream_calls(search_service):
    service, db, upstream_calls = search_service

    # First search -> misses cache and local, goes upstream (paginating across pages)
    outcome1 = await service.search_books("rust", user_id=1001)
    assert outcome1.source == "upstream"
    assert len(outcome1.hits) > 0
    first_calls = len(upstream_calls)
    assert first_calls >= 1

    # Second search -> warm cache hit, zero additional upstream calls
    outcome2 = await service.search_books("rust", user_id=1001)
    assert outcome2.source == "cache"
    assert len(outcome2.hits) == len(outcome1.hits)
    assert len(upstream_calls) == first_calls  # No additional calls


@pytest.mark.asyncio
async def test_local_fts_ge_threshold_still_queries_upstream_and_merges(test_config: Config):
    db = Database(test_config.db_path)
    db.init_schema()

    # Pre-populate 4 books locally (threshold is 3, but target results is 50)
    for i in range(4):
        await db.insert_book(title=f"Python Cookbook Vol {i}", author="David Beazley", md5=f"pycook{i}")

    upstream_calls: list[str] = []
    mock_html = """
    <table>
      <tr><th>Title</th><th>Author</th><th>Col3</th><th>Year</th><th>Lang</th><th>Col5</th><th>Size</th><th>Ext</th><th>Mirrors</th></tr>
      <tr><td>Python Cookbook Vol 99</td><td>David Beazley</td><td>c</td><td>2022</td><td>EN</td><td>c</td><td>3 MB</td><td>pdf</td><td><a href="ads.php?md5=pycook99">1</a></td></tr>
    </table>
    """

    def mock_handler(request: httpx.Request) -> httpx.Response:
        upstream_calls.append(str(request.url))
        return httpx.Response(200, text=mock_html)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    service = SearchService(
        db=db,
        config=test_config,
        mirror_manager=MirrorManager(db),
        single_flight=SingleFlight(),
        host_rate_limiter=HostRateLimiter(0),
        global_semaphore=create_global_semaphore(4),
        user_rate_limiter=UserRateLimiter(5, 0.5),
        http_client=client,
    )

    outcome = await service.search_books("Python Cookbook", user_id=1001)
    # Upstream was queried for freshness
    assert len(upstream_calls) >= 1
    assert outcome.source == "upstream"
    assert outcome.degraded is False
    titles = [b.title for b in outcome.hits]
    assert any("Vol 99" in t for t in titles)
    # Unrelated local catalog items are NOT merged on fresh upstream searches
    assert not any("Vol 0" in t for t in titles)


@pytest.mark.asyncio
async def test_fresh_query_does_not_merge_unrelated_local_hits(test_config: Config):
    db = Database(test_config.db_path)
    db.init_schema()

    # Pre-populate 1 book locally
    await db.insert_book(title="Local Rust Guide", author="Local Author", md5="local_rust_01")

    upstream_calls = []

    mock_html = """
    <table>
      <tr><th>Title</th><th>Author</th><th>Col3</th><th>Year</th><th>Lang</th><th>Col5</th><th>Size</th><th>Ext</th><th>Mirrors</th></tr>
      <tr><td>Rust Programming Language</td><td>Steve Klabnik</td><td>c</td><td>2018</td><td>EN</td><td>c</td><td>1 MB</td><td>epub</td><td><a href="ads.php?md5=7a7ef891b9d2b2ae8d9cd864556f7cd8">1</a></td></tr>
      <tr><td>Rust in Action</td><td>Tim McNamara</td><td>c</td><td>2021</td><td>EN</td><td>c</td><td>2 MB</td><td>pdf</td><td><a href="ads.php?md5=53a2781537e382ad99a4d250df99d1f5">1</a></td></tr>
    </table>
    """

    def mock_handler(request: httpx.Request) -> httpx.Response:
        upstream_calls.append(str(request.url))
        return httpx.Response(200, text=mock_html)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    service = SearchService(
        db=db,
        config=test_config,
        mirror_manager=MirrorManager(db),
        single_flight=SingleFlight(),
        host_rate_limiter=HostRateLimiter(0),
        global_semaphore=create_global_semaphore(4),
        user_rate_limiter=UserRateLimiter(5, 0.5),
        http_client=client,
    )

    outcome = await service.search_books("Rust", user_id=1001)
    assert outcome.source == "upstream"
    assert outcome.degraded is False
    assert len(upstream_calls) == 1
    # Check that fresh upstream hits are returned, without merging unrelated local catalogue hits
    titles = [b.title for b in outcome.hits]
    assert any("Programming Language" in t for t in titles)
    assert not any("Local Rust Guide" in t for t in titles)


@pytest.mark.asyncio
async def test_all_mirrors_fail_with_local_catalog_serves_degraded(test_config: Config):
    db = Database(test_config.db_path)
    db.init_schema()

    # Pre-populate a book locally (no cache)
    await db.insert_book(title="Offline Local Rust Guide", author="Local Author", md5="offline_rust_01")

    def mock_fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Server Error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_fail))
    service = SearchService(
        db=db,
        config=test_config,
        mirror_manager=MirrorManager(db),
        single_flight=SingleFlight(),
        host_rate_limiter=HostRateLimiter(0),
        global_semaphore=create_global_semaphore(4),
        user_rate_limiter=UserRateLimiter(5, 0.5),
        http_client=client,
    )

    outcome = await service.search_books("Rust", user_id=1001)
    assert outcome.source == "local"
    assert outcome.degraded is True
    assert outcome.hits[0].title == "Offline Local Rust Guide"


@pytest.mark.asyncio
async def test_all_mirrors_fail_with_stale_cache_serves_degraded(test_config: Config):
    db = Database(test_config.db_path)
    db.init_schema()

    # Seed an expired cache entry and existing book
    b = await db.insert_book(title="Stale Rust Book", author="Author", md5="stale123")
    # Expired 100 seconds ago
    await db.set_search_cache("rust", [b.id], ttl=-100.0)

    # Transport that always fails with 500
    def mock_fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Server Error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_fail))
    service = SearchService(
        db=db,
        config=test_config,
        mirror_manager=MirrorManager(db),
        single_flight=SingleFlight(),
        host_rate_limiter=HostRateLimiter(0),
        global_semaphore=create_global_semaphore(4),
        user_rate_limiter=UserRateLimiter(5, 0.5),
        http_client=client,
    )

    outcome = await service.search_books("rust", user_id=1001)
    assert outcome.source == "cache"
    assert outcome.degraded is True
    assert outcome.hits[0].title == "Stale Rust Book"


@pytest.mark.asyncio
async def test_all_mirrors_fail_with_nothing_raises(test_config: Config):
    db = Database(test_config.db_path)
    db.init_schema()

    def mock_fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Server Error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_fail))
    service = SearchService(
        db=db,
        config=test_config,
        mirror_manager=MirrorManager(db),
        single_flight=SingleFlight(),
        host_rate_limiter=HostRateLimiter(0),
        global_semaphore=create_global_semaphore(4),
        user_rate_limiter=UserRateLimiter(5, 0.5),
        http_client=client,
    )

    with pytest.raises(AllMirrorsFailed):
        await service.search_books("nonexistent", user_id=1001)


@pytest.mark.asyncio
async def test_user_rate_limiter_upstream_consumed_cache_not(search_service):
    service, db, _ = search_service
    user_id = 777

    # Warm search first for "rust"
    await service.search_books("rust", user_id=user_id)  # Consumed 1 token (4 left)

    # 4 distinct new queries consume remaining tokens
    for q in ["query1", "query2", "query3", "query4"]:
        await service.search_books(q, user_id=user_id)

    # 6th upstream search should raise RateLimitedError
    with pytest.raises(RateLimitedError):
        await service.search_books("query5", user_id=user_id)

    # But cached search for "rust" succeeds without raising RateLimitedError!
    cached_outcome = await service.search_books("rust", user_id=user_id)
    assert cached_outcome.source == "cache"


@pytest.mark.asyncio
async def test_empty_result_cache_short_ttl(test_config: Config):
    db = Database(test_config.db_path)
    db.init_schema()

    def mock_empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<table><tr><th>Title</th></tr></table>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_empty))
    service = SearchService(
        db=db,
        config=test_config,
        mirror_manager=MirrorManager(db),
        single_flight=SingleFlight(),
        host_rate_limiter=HostRateLimiter(0),
        global_semaphore=create_global_semaphore(4),
        user_rate_limiter=UserRateLimiter(5, 0.5),
        http_client=client,
    )

    t_before = time.time()
    outcome = await service.search_books("nonexistent_rare_query_xyz", user_id=1001)
    assert len(outcome.hits) == 0

    cached = await db.get_search_cache("nonexistent_rare_query_xyz")
    assert cached is not None
    assert cached.book_ids == []
    # Empty cache TTL should be close to empty_result_cache_ttl (300s), not 3600s
    remaining_ttl = cached.expires_at - t_before
    assert 290 <= remaining_ttl <= 310


@pytest.mark.asyncio
async def test_relaxed_query_fallback_on_zero_hits(test_config: Config):
    db = Database(test_config.db_path)
    db.init_schema()

    # If query is strict, return empty table; if query is relaxed, return a hit
    mock_hit_html = """
    <table>
      <tr><th>Title</th><th>Author</th><th>Col3</th><th>Year</th><th>Lang</th><th>Col5</th><th>Size</th><th>Ext</th><th>Mirrors</th></tr>
      <tr><td>Rust Programming</td><td>Steve Klabnik</td><td>c</td><td>2021</td><td>EN</td><td>c</td><td>2 MB</td><td>pdf</td><td><a href="ads.php?md5=7a7ef891b9d2b2ae8d9cd864556f7cd8">1</a></td></tr>
    </table>
    """

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url).lower()
        if "practice" in url_str and "the" in url_str:
            # Strict query with stop words produces 0 hits
            return httpx.Response(200, text="<table><tr><th>Title</th></tr></table>")
        # Relaxed query without stop words produces hits
        return httpx.Response(200, text=mock_hit_html)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    service = SearchService(
        db=db,
        config=test_config,
        mirror_manager=MirrorManager(db),
        single_flight=SingleFlight(),
        host_rate_limiter=HostRateLimiter(0),
        global_semaphore=create_global_semaphore(4),
        user_rate_limiter=UserRateLimiter(5, 0.5),
        http_client=client,
    )

    # "The Rust in Practice" relaxes by removing "the" and "in" -> "rust practice"
    outcome = await service.search_books("The Rust in Practice", user_id=1001)
    assert len(outcome.hits) > 0
    assert outcome.is_relaxed is True


@pytest.mark.asyncio
async def test_partial_results_preserved_when_later_page_fails(test_config: Config):
    db = Database(test_config.db_path)
    db.init_schema()

    rows = "\n".join(
        f'<tr><td>Rust Async Programming Vol {i}</td><td>Author</td><td>c</td><td>2022</td><td>EN</td><td>c</td><td>2 MB</td><td>pdf</td><td><a href="ads.php?md5={i:032x}">1</a></td></tr>'
        for i in range(25)
    )
    page1_html = f"""
    <table>
      <tr><th>Title</th><th>Author</th><th>Col3</th><th>Year</th><th>Lang</th><th>Col5</th><th>Size</th><th>Ext</th><th>Mirrors</th></tr>
      {rows}
    </table>
    """

    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if "page=2" in str(request.url):
            # Page 2 fails with 502 Bad Gateway
            return httpx.Response(502, text="Bad Gateway")
        return httpx.Response(200, text=page1_html)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    service = SearchService(
        db=db,
        config=test_config,
        mirror_manager=MirrorManager(db),
        single_flight=SingleFlight(),
        host_rate_limiter=HostRateLimiter(0),
        global_semaphore=create_global_semaphore(4),
        user_rate_limiter=UserRateLimiter(5, 0.5),
        http_client=client,
    )

    outcome = await service.search_books("Rust Async", user_id=1001)
    # Page 1 hits (25 books) are returned cleanly even though page 2 threw an error
    assert len(outcome.hits) == 25
    assert "Rust Async Programming" in outcome.hits[0].title
    assert outcome.source == "upstream"
    assert call_count >= 2


@pytest.mark.asyncio
async def test_challenge_response_does_not_poison_empty_cache(test_config: Config):
    db = Database(test_config.db_path)
    db.init_schema()

    # Mirror returns nginx default error page
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><head><title>Welcome to nginx!</title></head></html>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    service = SearchService(
        db=db,
        config=test_config,
        mirror_manager=MirrorManager(db),
        single_flight=SingleFlight(),
        host_rate_limiter=HostRateLimiter(0),
        global_semaphore=create_global_semaphore(4),
        user_rate_limiter=UserRateLimiter(5, 0.5),
        http_client=client,
    )

    # All mirrors fail because challenge page is raised as UpstreamInvalidResponseError
    with pytest.raises(AllMirrorsFailed):
        await service.search_books("nginx_fail_query", user_id=1001)

    # Crucial: search_cache must NOT contain empty results for this query
    cached = await db.get_search_cache("nginx_fail_query")
    assert cached is None


@pytest.mark.asyncio
async def test_relaxed_query_does_not_consume_second_token(test_config: Config):
    db = Database(test_config.db_path)
    db.init_schema()

    mock_hit_html = """
    <table>
      <tr><th>Title</th><th>Author</th><th>Col3</th><th>Year</th><th>Lang</th><th>Col5</th><th>Size</th><th>Ext</th><th>Mirrors</th></tr>
      <tr><td>Rust Programming</td><td>Author</td><td>c</td><td>2021</td><td>EN</td><td>c</td><td>2 MB</td><td>pdf</td><td><a href="ads.php?md5=7a7ef891b9d2b2ae8d9cd864556f7cd8">1</a></td></tr>
    </table>
    """

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url).lower()
        if "cookbook" in url_str and "the" in url_str:
            return httpx.Response(200, text="<table><tr><th>Title</th></tr></table>")
        return httpx.Response(200, text=mock_hit_html)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    # Give user exactly 1.0 token
    user_limiter = UserRateLimiter(max_tokens=1.0, refill_per_sec=0.0)
    service = SearchService(
        db=db,
        config=test_config,
        mirror_manager=MirrorManager(db),
        single_flight=SingleFlight(),
        host_rate_limiter=HostRateLimiter(0),
        global_semaphore=create_global_semaphore(4),
        user_rate_limiter=user_limiter,
        http_client=client,
    )

    # Strict query has 0 hits, triggers relaxation.
    # If relaxation deducted a second token, it would fail with RateLimitedError.
    # It must succeed because relaxation uses the original request's authorization!
    outcome = await service.search_books("The Rust Cookbook", user_id=1001)
    assert len(outcome.hits) > 0
    assert outcome.is_relaxed is True


@pytest.mark.asyncio
async def test_search_service_aclose(test_config: Config):
    db = Database(test_config.db_path)
    client = httpx.AsyncClient()
    service = SearchService(
        db=db,
        config=test_config,
        mirror_manager=MirrorManager(db),
        single_flight=SingleFlight(),
        host_rate_limiter=HostRateLimiter(0),
        global_semaphore=create_global_semaphore(4),
        http_client=client,
    )
    assert not client.is_closed
    await service.aclose()
    assert client.is_closed


@pytest.mark.asyncio
async def test_mirror_manager_aclose(test_config: Config):
    db = Database(test_config.db_path)
    client = httpx.AsyncClient()
    mm = MirrorManager(db, http_client=client)
    assert not client.is_closed
    await mm.aclose()
    assert client.is_closed


