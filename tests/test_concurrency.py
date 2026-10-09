import asyncio
import pytest
from concurrency import SingleFlight, HostRateLimiter, UserRateLimiter, create_global_semaphore


@pytest.mark.asyncio
async def test_single_flight_deduplication():
    sf = SingleFlight()
    call_count = 0

    async def fetch():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return "result_data"

    # Launch 10 concurrent calls with the same key
    tasks = [asyncio.create_task(sf.do("search:rust", fetch)) for _ in range(10)]
    results = await asyncio.gather(*tasks)

    assert len(results) == 10
    assert all(r == "result_data" for r in results)
    assert call_count == 1  # Exactly 1 upstream call occurred


@pytest.mark.asyncio
async def test_single_flight_exception_and_no_leak():
    sf = SingleFlight()
    call_count = 0

    async def fail_call():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.02)
        raise RuntimeError("Upstream mirror blew up")

    tasks = [asyncio.create_task(sf.do("search:fail", fail_call)) for _ in range(5)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert len(results) == 5
    for r in results:
        assert isinstance(r, RuntimeError)
        assert str(r) == "Upstream mirror blew up"

    assert call_count == 1
    # Check that the key is popped and not leaked
    assert "search:fail" not in sf._futures

    # Subsequent call can execute fresh without trapping into dead future
    async def succeed():
        return "recovered"

    res = await sf.do("search:fail", succeed)
    assert res == "recovered"


@pytest.mark.asyncio
async def test_user_rate_limiter_token_bucket():
    # 5 tokens, refill 0.5 per sec
    limiter = UserRateLimiter(max_tokens=5.0, refill_per_sec=0.5)
    user_id = 12345

    # 5 requests should succeed
    for i in range(5):
        allowed = await limiter.acquire(user_id)
        assert allowed is True, f"Request {i+1} should be allowed"

    # 6th immediate request should be refused
    allowed_6th = await limiter.acquire(user_id)
    assert allowed_6th is False

    # Different user is unaffected
    other_user = 99999
    assert (await limiter.acquire(other_user)) is True


@pytest.mark.asyncio
async def test_host_rate_limiter():
    limiter = HostRateLimiter(polite_delay_ms=50)
    timestamps = []

    async def hit(host: str):
        async with limiter.acquire(host):
            timestamps.append(asyncio.get_running_loop().time())

    # Two requests to same host serialized
    await asyncio.gather(hit("libgen.li"), hit("libgen.li"))
    assert len(timestamps) == 2
    delta = timestamps[1] - timestamps[0]
    assert delta >= 0.045  # At least ~50ms apart
