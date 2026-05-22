"""
Shared httpx.AsyncClient — one connection pool for the entire process.
Initialize on startup via main.py lifespan; close on shutdown.
"""
import httpx

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _client


async def close_http_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None
