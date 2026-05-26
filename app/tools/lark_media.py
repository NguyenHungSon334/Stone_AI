"""
Lark Bitable media fetcher.

get_product_media_urls(ma_sp) → {"anh": [url, ...], "video": [url, ...]}

URLs are tmp_download_urls (public, ~10 min TTL) — long enough for Messenger to fetch.
Token is cached and auto-refreshed (2h TTL).
"""
from __future__ import annotations

import time
import urllib.parse
from loguru import logger

from app.config import settings
from app.http_client import get_http_client

_LARK_BASE = "https://open.larksuite.com/open-apis"

# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------

_token_cache: dict = {"token": "", "expires_at": 0.0}


async def _get_token() -> str:
    now = time.monotonic()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    client = get_http_client()
    r = await client.post(
        f"{_LARK_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": settings.lark_app_id, "app_secret": settings.lark_app_secret},
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lark token error: {data.get('msg')}")

    token = data["tenant_access_token"]
    expire = int(data.get("expire", 7200))
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + expire - 120  # refresh 2 min early
    logger.info("Lark token refreshed expire={}s", expire)
    return token


# ---------------------------------------------------------------------------
# Bitable query
# ---------------------------------------------------------------------------

async def _query_record(token: str, ma_sp: str) -> dict | None:
    client = get_http_client()
    r = await client.get(
        f"{_LARK_BASE}/bitable/v1/apps/{settings.lark_bitable_app_token}"
        f"/tables/{settings.lark_bitable_table_id}/records",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "filter": f'CurrentValue.[Mã Sản Phẩm]="{ma_sp.strip().upper()}"',
            "page_size": 1,
        },
    )
    data = r.json()
    if data.get("code") != 0:
        logger.warning("Lark bitable query error: {}", data.get("msg"))
        return None
    items = data.get("data", {}).get("items", [])
    return items[0]["fields"] if items else None


# ---------------------------------------------------------------------------
# Tmp download URLs
# ---------------------------------------------------------------------------

def _extract_extra(att_url: str) -> str:
    """Pull the extra=... JSON from the attachment url field."""
    if "extra=" not in att_url:
        return ""
    raw = att_url.split("extra=")[1].split("&")[0]
    return urllib.parse.unquote(raw)


async def _get_one_tmp_url(client, token: str, att: dict) -> str:
    """Call each attachment's pre-built tmp_url to get a public download URL."""
    att_tmp_url = att.get("tmp_url", "")
    if not att_tmp_url:
        return ""
    try:
        r = await client.get(att_tmp_url, headers={"Authorization": f"Bearer {token}"})
        data = r.json()
        if data.get("code") != 0:
            return ""
        urls = data.get("data", {}).get("tmp_download_urls", [])
        return urls[0]["tmp_download_url"] if urls else ""
    except Exception:
        return ""


async def _get_tmp_urls(token: str, attachments: list[dict]) -> list[str]:
    if not attachments:
        return []
    client = get_http_client()
    import asyncio as _asyncio
    results = await _asyncio.gather(
        *[_get_one_tmp_url(client, token, att) for att in attachments]
    )
    return [u for u in results if u]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_product_media_urls(ma_sp: str) -> dict[str, list[str]]:
    """
    Return tmp_download_urls for a product's images and videos.
    URLs are public (~10 min TTL) — suitable for Messenger send API.
    Returns {"anh": [...], "video": [...]}
    """
    if not settings.lark_app_id:
        logger.warning("Lark credentials not configured")
        return {"anh": [], "video": []}

    try:
        token = await _get_token()
        fields = await _query_record(token, ma_sp)
        if not fields:
            logger.info("Lark: no record for ma_sp={}", ma_sp)
            return {"anh": [], "video": []}

        anh_att = fields.get("Ảnh") or []
        baogia_att = fields.get("Ảnh báo giá(1 ảnh rõ sản phẩm)") or []
        video_att = fields.get("Video") or []

        # Báo giá first (cleaner single photo), then rest of Ảnh
        combined_anh = baogia_att + anh_att
        anh_urls, video_urls = await _get_tmp_urls(token, combined_anh), await _get_tmp_urls(token, video_att)

        logger.info("Lark media ma_sp={} anh={} video={}", ma_sp, len(anh_urls), len(video_urls))
        return {"anh": anh_urls, "video": video_urls}

    except Exception:
        logger.exception("Lark media fetch failed for ma_sp={}", ma_sp)
        return {"anh": [], "video": []}
