"""Async Kubo (IPFS) RPC client for Nabu-V1.

Implements:
- Streaming retrieval via POST /api/v0/cat?arg=<CID>
- Hard 90-second deadline enforcement using asyncio.timeout
- Mid-stream byte counting with abortion on oversize files (> 50 MB)
- Add/ingest operation via POST /api/v0/add?cid-version=1&raw-leaves=true&pin=true
- Pin operation via POST /api/v0/pin/add?arg=<CID>
- Public gateway fallback for source-supplied CIDs
- Daemon health checks via POST /api/v0/version
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
import httpx

logger = logging.getLogger(__name__)


class KuboError(Exception):
    """Base exception for Kubo/IPFS operations."""


class KuboTimeoutError(KuboError):
    """Raised when a Kubo or gateway retrieval exceeds the hard deadline."""


class KuboOfflineError(KuboError):
    """Raised when the local Kubo daemon cannot be contacted."""


class OversizeFileError(KuboError):
    """Raised when a file exceeds Telegram's 50 MB delivery limit."""


class CIDNotFoundError(KuboError):
    """Raised when a CID cannot be found or resolved."""


class KuboClient:
    """Async HTTP client for interacting with the local Kubo daemon and gateways."""

    def __init__(
        self,
        api_url: str = "http://127.0.0.1:5001",
        public_gateways: tuple[str, ...] = ("https://ipfs.io/ipfs", "https://dweb.link/ipfs"),
        download_timeout: float = 90.0,
        max_file_size: int = 52_428_800,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.public_gateways = public_gateways
        self.download_timeout = download_timeout
        self.max_file_size = max_file_size

        # Persistent reusable AsyncClient with reasonable connection pooling
        limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
        self._client = httpx.AsyncClient(
            base_url=self.api_url,
            limits=limits,
            transport=transport,
            timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0),
        )
        # Dedicated client for external public gateways
        self._gateway_client = httpx.AsyncClient(
            limits=limits,
            transport=transport,
            timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=5.0),
        )

    async def close(self) -> None:
        """Close HTTP clients gracefully."""
        await self._client.aclose()
        await self._gateway_client.aclose()

    async def is_online(self) -> bool:
        """Check if local Kubo RPC daemon is responsive."""
        try:
            resp = await self._client.post("/api/v0/version", timeout=3.0)
            return resp.status_code == 200
        except (httpx.RequestError, httpx.TimeoutException):
            return False

    async def cat_file(
        self,
        cid: str,
        dest_path: Path,
        max_bytes: int | None = None,
        timeout: float | None = None,
        allow_gateway_fallback: bool = True,
    ) -> int:
        """Stream a CID from the local Kubo daemon to dest_path.

        Enforces:
        1. Hard deadline wrapped in asyncio.timeout.
        2. Mid-stream byte counting aborting if max_bytes is exceeded.
        3. Automatic gateway fallback retry if local Kubo times out or fails.
        4. Strict temporary file cleanup on any failure.

        Returns:
            Total bytes written to dest_path.
        """
        limit = max_bytes if max_bytes is not None else self.max_file_size
        deadline = timeout if timeout is not None else self.download_timeout
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Attempt 1: Local Kubo RPC node
        try:
            return await self._cat_local(cid, dest_path, limit, deadline)
        except (KuboTimeoutError, KuboOfflineError, CIDNotFoundError) as exc:
            if not allow_gateway_fallback or not self.public_gateways:
                raise

            logger.warning(
                "Local Kubo cat failed for %s (%s). Attempting public gateway fallback.",
                cid,
                exc,
            )
            # Attempt 2: Public gateway fallback
            return await self._cat_gateway(cid, dest_path, limit, deadline)

    async def _cat_local(
        self,
        cid: str,
        dest_path: Path,
        max_bytes: int,
        deadline: float,
    ) -> int:
        total_written = 0
        try:
            async with asyncio.timeout(deadline):
                async with self._client.stream("POST", "/api/v0/cat", params={"arg": cid}) as resp:
                    if resp.status_code == 404:
                        raise CIDNotFoundError(f"CID {cid} not found on IPFS")
                    if resp.status_code != 200:
                        err_text = await resp.aread()
                        raise KuboError(
                            f"Kubo cat failed with HTTP {resp.status_code}: {err_text.decode('utf-8', errors='replace')}"
                        )

                    with open(dest_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            total_written += len(chunk)
                            if total_written > max_bytes:
                                raise OversizeFileError(
                                    f"File size exceeded limit ({total_written} > {max_bytes} bytes)"
                                )
                            f.write(chunk)

            logger.info("Successfully retrieved CID %s via local Kubo (%d bytes)", cid, total_written)
            return total_written

        except TimeoutError as exc:
            self._cleanup_partial(dest_path)
            raise KuboTimeoutError(f"Kubo retrieval for CID {cid} timed out after {deadline}s") from exc
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            self._cleanup_partial(dest_path)
            raise KuboOfflineError(f"Cannot connect to local Kubo daemon at {self.api_url}") from exc
        except Exception:
            self._cleanup_partial(dest_path)
            raise

    async def _cat_gateway(
        self,
        cid: str,
        dest_path: Path,
        max_bytes: int,
        deadline: float,
    ) -> int:
        for gateway in self.public_gateways:
            url = f"{gateway.rstrip('/')}/{cid}"
            total_written = 0
            try:
                async with asyncio.timeout(deadline):
                    async with self._gateway_client.stream("GET", url) as resp:
                        if resp.status_code != 200:
                            continue

                        with open(dest_path, "wb") as f:
                            async for chunk in resp.aiter_bytes(chunk_size=65536):
                                total_written += len(chunk)
                                if total_written > max_bytes:
                                    raise OversizeFileError(
                                        f"File size exceeded limit ({total_written} > {max_bytes} bytes)"
                                    )
                                f.write(chunk)

                logger.info("Successfully retrieved CID %s via gateway %s (%d bytes)", cid, gateway, total_written)
                return total_written

            except TimeoutError:
                self._cleanup_partial(dest_path)
                logger.warning("Gateway %s timed out for CID %s", gateway, cid)
                continue
            except OversizeFileError:
                self._cleanup_partial(dest_path)
                raise
            except Exception as exc:
                self._cleanup_partial(dest_path)
                logger.warning("Gateway %s failed for CID %s: %s", gateway, cid, exc)
                continue

        self._cleanup_partial(dest_path)
        raise KuboTimeoutError(f"All public gateways failed or timed out for CID {cid}")

    async def add_file(self, file_path: Path, pin: bool = True) -> str:
        """Ingest a file into Kubo and return its CID.

        Uses: POST /api/v0/add?cid-version=1&raw-leaves=true&pin=true
        Produces CIDv1 base32 matching Anna's Archive format.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        params = {
            "cid-version": "1",
            "raw-leaves": "true",
            "pin": "true" if pin else "false",
            "wrap-with-directory": "false",
        }

        try:
            with open(file_path, "rb") as f:
                files = {"file": (file_path.name, f, "application/octet-stream")}
                resp = await self._client.post(
                    "/api/v0/add",
                    params=params,
                    files=files,
                    timeout=httpx.Timeout(120.0),
                )

            if resp.status_code != 200:
                raise KuboError(
                    f"Kubo add failed with HTTP {resp.status_code}: {resp.text}"
                )

            data = resp.json()
            cid = data.get("Hash")
            if not cid:
                raise KuboError(f"Kubo add did not return a Hash: {data}")

            logger.info("Ingested %s into Kubo as CID %s", file_path.name, cid)
            return cid

        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise KuboOfflineError(f"Cannot connect to local Kubo daemon at {self.api_url}") from exc

    async def pin_cid(self, cid: str) -> bool:
        """Pin a source-supplied CID in the local Kubo node.

        Uses: POST /api/v0/pin/add?arg=<CID>
        """
        try:
            resp = await self._client.post(
                "/api/v0/pin/add",
                params={"arg": cid},
                timeout=httpx.Timeout(60.0),
            )
            if resp.status_code == 200:
                logger.info("Pinned CID %s in local Kubo node", cid)
                return True
            logger.warning("Kubo pin/add returned HTTP %d for CID %s: %s", resp.status_code, cid, resp.text)
            return False
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise KuboOfflineError(f"Cannot connect to local Kubo daemon at {self.api_url}") from exc
        except Exception as exc:
            logger.warning("Failed to pin CID %s: %s", cid, exc)
            return False

    @staticmethod
    def _cleanup_partial(path: Path) -> None:
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
