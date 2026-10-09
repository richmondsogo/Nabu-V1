"""Comprehensive tests for ipfs.py using httpx.MockTransport."""

import asyncio
from pathlib import Path
import pytest
import httpx

from ipfs import (
    CIDNotFoundError,
    KuboClient,
    KuboOfflineError,
    KuboTimeoutError,
    OversizeFileError,
)


@pytest.mark.asyncio
async def test_kubo_is_online_true():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v0/version"
        return httpx.Response(200, json={"Version": "0.26.0"})

    transport = httpx.MockTransport(handler)
    client = KuboClient(transport=transport)
    try:
        assert await client.is_online() is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_kubo_is_online_false_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    transport = httpx.MockTransport(handler)
    client = KuboClient(transport=transport)
    try:
        assert await client.is_online() is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cat_file_success(tmp_path: Path):
    dest = tmp_path / "test.pdf"
    content = b"%PDF-1.4 sample content " * 100

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v0/cat"
        assert request.url.params["arg"] == "bafybeiexamplecid"
        return httpx.Response(200, content=content)

    transport = httpx.MockTransport(handler)
    client = KuboClient(transport=transport)
    try:
        bytes_written = await client.cat_file("bafybeiexamplecid", dest)
        assert bytes_written == len(content)
        assert dest.exists()
        assert dest.read_bytes() == content
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cat_file_oversize_abort(tmp_path: Path):
    dest = tmp_path / "oversize.part"
    large_chunk = b"A" * 1000

    def handler(request: httpx.Request) -> httpx.Response:
        # Generate stream of 5000 bytes
        return httpx.Response(200, content=large_chunk * 5)

    transport = httpx.MockTransport(handler)
    # Set max_file_size to 2000 bytes
    client = KuboClient(transport=transport, max_file_size=2000)
    try:
        with pytest.raises(OversizeFileError, match="exceeded limit"):
            await client.cat_file("bafybeioversize", dest, allow_gateway_fallback=False)

        # Crucial invariant: partial file MUST be deleted on abort
        assert not dest.exists()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cat_file_timeout(tmp_path: Path):
    dest = tmp_path / "timeout.part"

    async def slow_stream():
        yield b"chunk 1"
        await asyncio.sleep(0.5)
        yield b"chunk 2"

    async def async_handler(request: httpx.Request) -> httpx.Response:
        # Simulate very slow response
        return httpx.Response(200, content=slow_stream())

    transport = httpx.MockTransport(async_handler)
    # Deadline 0.1s
    client = KuboClient(transport=transport, download_timeout=0.1)
    try:
        with pytest.raises(KuboTimeoutError, match="timed out"):
            await client.cat_file("bafybeitimeout", dest, timeout=0.1, allow_gateway_fallback=False)

        # Crucial invariant: partial file MUST be deleted on timeout
        assert not dest.exists()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cat_file_gateway_fallback(tmp_path: Path):
    dest = tmp_path / "gateway_fallback.pdf"
    content = b"%PDF-1.4 from public gateway"

    def handler(request: httpx.Request) -> httpx.Response:
        # Local Kubo daemon is offline or throws ConnectError
        if "127.0.0.1" in str(request.url):
            raise httpx.ConnectError("Daemon offline")
        # Public gateway handles it
        if "ipfs.io" in str(request.url):
            assert "bafybeigatewaycid" in str(request.url)
            return httpx.Response(200, content=content)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = KuboClient(
        transport=transport,
        public_gateways=("https://ipfs.io/ipfs",),
    )
    try:
        bytes_written = await client.cat_file("bafybeigatewaycid", dest, allow_gateway_fallback=True)
        assert bytes_written == len(content)
        assert dest.exists()
        assert dest.read_bytes() == content
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_add_file_success(tmp_path: Path):
    book_file = tmp_path / "clean_code.pdf"
    book_file.write_bytes(b"%PDF-1.7 sample data")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v0/add"
        assert request.url.params["cid-version"] == "1"
        assert request.url.params["raw-leaves"] == "true"
        assert request.url.params["pin"] == "true"
        return httpx.Response(200, json={"Hash": "bafybeicidv1result", "Name": "clean_code.pdf", "Size": "20"})

    transport = httpx.MockTransport(handler)
    client = KuboClient(transport=transport)
    try:
        cid = await client.add_file(book_file, pin=True)
        assert cid == "bafybeicidv1result"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_pin_cid_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v0/pin/add"
        assert request.url.params["arg"] == "bafybeipinmecid"
        return httpx.Response(200, json={"Pins": ["bafybeipinmecid"]})

    transport = httpx.MockTransport(handler)
    client = KuboClient(transport=transport)
    try:
        ok = await client.pin_cid("bafybeipinmecid")
        assert ok is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_pin_cid_failure_handled():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal pinning failure")

    transport = httpx.MockTransport(handler)
    client = KuboClient(transport=transport)
    try:
        ok = await client.pin_cid("bafybeibadcid")
        assert ok is False
    finally:
        await client.close()
