"""
Product search via direct DB query (ILIKE keyword matching).
"""
from __future__ import annotations

import re

from loguru import logger

from app.db.supabase import get_client

_STOPWORDS = {
    "cho", "tôi", "em", "anh", "chị", "bác", "về", "của", "và", "hoặc",
    "có", "không", "một", "cái", "bộ", "hỏi", "muốn", "xem", "cần",
    "được", "thì", "là", "ở", "tại", "với", "các", "những", "này",
    "loại", "sản", "phẩm", "đá", "hàng",
}

_SELECT_COLS = (
    "id, ma_sp, ten_sp, the_loai, danh_muc, kich_thuoc, "
    "gia_da_xanh_den, gia_da_xanh_reu, gia_da_xam_bd, gia_da_grn_an_do, "
    "mo_ta, ghi_chu, tags, ton_kho, ban_chay"
)

_TEXT_COLS = ["ten_sp", "mo_ta", "the_loai", "danh_muc", "ghi_chu", "kich_thuoc"]


# ---------------------------------------------------------------------------
# Budget parsing
# ---------------------------------------------------------------------------

def _parse_budget(budget_text: str | None) -> int | None:
    """Convert slot budget text to max price in VND. Returns None if unparseable."""
    if not budget_text:
        return None
    text = budget_text.lower().replace(" ", "").replace(",", ".")

    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:tr(?:iệu)?|m(?:illion)?)\b", text)
    if m:
        return int(float(m.group(1)) * 1_000_000)

    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:k|nghìn|ngàn)\b", text)
    if m:
        return int(float(m.group(1)) * 1_000)

    m = re.search(r"(\d{4,})", text)
    if m:
        return int(m.group(1))

    return None


def _extract_keywords(query: str) -> list[str]:
    """Extract meaningful keywords from query, max 4."""
    words = re.sub(r"[.,?!]", " ", query.lower()).split()
    return [w for w in words if len(w) >= 2 and w not in _STOPWORDS][:4]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def search_products(query: str, slots: dict, n: int = 6) -> list[dict]:
    """
    Text-based DB search: each keyword must appear in at least one product text column.
    Falls back to bestsellers if no results found.
    """
    client = get_client()
    keywords = _extract_keywords(query)

    # Also extract hints from slots
    the_loai = slots.get("product_type") or slots.get("stone_type") or slots.get("project_type")
    budget = _parse_budget(slots.get("budget"))

    products = _run_query(client, keywords, the_loai, budget, n)

    # Fallback 1: drop keywords one by one if no results
    kws = list(keywords)
    while not products and len(kws) > 1:
        kws = kws[:-1]
        products = _run_query(client, kws, the_loai, budget, n)

    # Fallback 2: bestsellers
    if not products:
        products = _run_query(client, [], the_loai, budget, n)

    logger.info("search found {} products query={!r} keywords={}", len(products), query[:50], keywords)
    return products


def _run_query(client, keywords: list[str], the_loai: str | None, budget: int | None, n: int) -> list[dict]:
    """Build and execute one Supabase query."""
    try:
        q = client.table("products").select(_SELECT_COLS)

        # AND across keywords: each keyword must match at least one column
        for kw in keywords:
            or_parts = ",".join(f"{col}.ilike.%{kw}%" for col in _TEXT_COLS)
            q = q.or_(or_parts)

        # the_loai hard filter from slot
        if the_loai:
            q = q.ilike("the_loai", f"%{the_loai}%")

        # Budget: at least one price column must be within budget
        if budget:
            price_cols = [
                "gia_da_xanh_den", "gia_da_xanh_reu",
                "gia_da_xam_bd", "gia_da_grn_an_do",
            ]
            price_filter = ",".join(f"{col}.lte.{budget}" for col in price_cols)
            q = q.or_(price_filter)

        result = q.order("ban_chay", desc=True).limit(n).execute()
        return result.data or []
    except Exception:
        logger.exception("DB query failed keywords={} the_loai={}", keywords, the_loai)
        return []


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _best_price(p: dict) -> int | None:
    candidates = [
        p.get("gia_da_xanh_den"),
        p.get("gia_da_xanh_reu"),
        p.get("gia_da_xam_bd"),
        p.get("gia_da_grn_an_do"),
    ]
    valid = [c for c in candidates if c]
    return min(valid) if valid else None


def format_results(products: list[dict]) -> str:
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
    if not products:
        return "Không tìm thấy sản phẩm phù hợp."
    lines = ["Sản phẩm gợi ý từ kho hàng Hồn Đá:"]
    for p in products[:5]:
        price = _best_price(p)
        price_str = f"{price:,}₫" if price else "Liên hệ"
        desc = p.get("mo_ta") or ""
        line = (
            f"- {p['ten_sp']} | {p.get('kich_thuoc', '')} "
            f"| Từ {price_str} | {desc[:60]}"
        )
        lines.append(line)
    return "\n".join(lines)
