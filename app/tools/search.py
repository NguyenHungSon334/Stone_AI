"""
Product search via pgvector semantic similarity + result formatting.
"""
from __future__ import annotations

import re

import httpx
from loguru import logger

from app.config import settings
from app.db.supabase import get_client

_EMBED_URL = "https://openrouter.ai/api/v1/embeddings"
_EMBED_MODEL = "openai/text-embedding-3-small"


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

async def embed_query(text: str) -> list[float]:
    """Embed a search query via OpenRouter."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            _EMBED_URL,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={"model": _EMBED_MODEL, "input": text},
        )
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]


# ---------------------------------------------------------------------------
# Budget parsing
# ---------------------------------------------------------------------------

def _parse_budget(budget_text: str | None) -> int | None:
    """Convert slot budget text to max price in VND. Returns None if unparseable."""
    if not budget_text:
        return None
    text = budget_text.lower().replace(" ", "").replace(",", ".")

    # Millions: "2tr", "1.5triệu", "2m"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:tr(?:iệu)?|m(?:illion)?)\b", text)
    if m:
        return int(float(m.group(1)) * 1_000_000)

    # Thousands: "500k", "300nghìn"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:k|nghìn|ngàn)\b", text)
    if m:
        return int(float(m.group(1)) * 1_000)

    # Plain number heuristic
    m = re.search(r"(\d{4,})", text)
    if m:
        return int(m.group(1))

    return None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def search_products(query: str, slots: dict, n: int = 5) -> list[dict]:
    """
    Embed query and call match_products RPC.
    Returns products ordered by cosine similarity (best match first).
    """
    try:
        embedding = await embed_query(query)
    except Exception:
        logger.exception("embed_query failed query={!r}", query[:80])
        return []

    params = {
        "query_embedding": embedding,
        "match_count": n,
        "filter_the_loai": slots.get("product_type"),
        "filter_danh_muc": None,
        "filter_price_max": _parse_budget(slots.get("budget")),
    }

    try:
        result = get_client().rpc("match_products", params).execute()
        products = result.data or []
        logger.info("search found {} products query={!r}", len(products), query[:50])
        return products
    except Exception:
        logger.exception("match_products RPC failed")
        return []


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _best_price(p: dict) -> int | None:
    """Return the lowest non-null price across stone variants."""
    candidates = [
        p.get("gia_da_xanh_den"),
        p.get("gia_da_xanh_reu"),
        p.get("gia_da_xam_bd"),
        p.get("gia_da_grn_an_do"),
    ]
    valid = [c for c in candidates if c]
    return min(valid) if valid else None


def format_results(products: list[dict]) -> str:
    """Format product list as concise Messenger-friendly text."""
    if not products:
        return (
            "Em chưa tìm thấy sản phẩm phù hợp ạ. "
            "Anh/chị có thể cho em biết thêm về mục đích sử dụng hoặc ngân sách không?"
        )

    lines = ["Em tìm thấy một số sản phẩm phù hợp:\n"]
    for i, p in enumerate(products[:3], 1):
        name = p.get("ten_sp", "")
        size = p.get("kich_thuoc", "")
        price = _best_price(p)
        price_str = f"{price:,}₫" if price else "Liên hệ"
        desc = p.get("mo_ta") or p.get("ghi_chu") or ""
        desc_str = (desc[:80] + "…") if len(desc) > 80 else desc

        line = f"{i}. {name}"
        if size:
            line += f" ({size})"
        line += f"\n   💰 {price_str}"
        if desc_str:
            line += f"\n   {desc_str}"
        lines.append(line)

    if len(products) > 3:
        lines.append(f"\n…và {len(products) - 3} sản phẩm khác. Anh/chị muốn xem thêm không ạ?")

    return "\n".join(lines)


def format_products_for_llm(products: list[dict]) -> str:
    """Compact product list for injection into LLM system context."""
    if not products:
        return "Không tìm thấy sản phẩm phù hợp."
    lines = ["Sản phẩm gợi ý từ kho hàng Hồn Đá:"]
    for p in products[:5]:
        price = _best_price(p)
        price_str = f"{price:,}₫" if price else "Liên hệ"
        desc = p.get("mo_ta") or ""
        line = f"- {p['ten_sp']} | {p.get('kich_thuoc','')}" \
               f" | Từ {price_str} | {desc[:60]}"
        lines.append(line)
    return "\n".join(lines)
