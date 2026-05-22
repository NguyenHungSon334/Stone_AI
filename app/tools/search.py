"""
Hybrid product search: pgvector (semantic) + SQL (price, dimensions, the_loai).

Strategy:
  1. Extract hard SQL filters from slots (the_loai, budget, dimensions)
  2. Semantic query via pgvector (match_products RPC)
  3. Fallback: ILIKE on search_text (pg_trgm index)
  4. Final fallback: bestsellers with SQL filters only
"""
from __future__ import annotations

import asyncio
import re

from loguru import logger

from app.db.supabase import get_client
from app.llm import embed

_SELECT_COLS = (
    "id, ma_sp, ten_sp, the_loai, danh_muc, kich_thuoc, "
    "chieu_dai_mm, chieu_rong_mm, chieu_cao_mm, "
    "gia_da_xanh_den, gia_da_xanh_reu, gia_da_xam_bd, gia_da_grn_an_do, "
    "mo_ta, ghi_chu, tags, ton_kho, ban_chay"
)

_STOPWORDS = {
    "cho", "tôi", "em", "anh", "chị", "bác", "về", "của", "và", "hoặc",
    "có", "không", "một", "cái", "bộ", "hỏi", "muốn", "xem", "cần",
    "được", "thì", "là", "ở", "tại", "với", "các", "những", "này",
    "loại", "sản", "phẩm", "đá", "hàng", "giá", "bao", "nhiêu",
}

# Customer-facing the_loai names (exact values in DB)
# stone_type slot value → price column that is non-null for that stone
_STONE_COL_MAP: dict[str, str] = {
    "xanh đen":  "gia_da_xanh_den",
    "xanh den":  "gia_da_xanh_den",
    "đen":       "gia_da_xanh_den",
    "xanh rêu":  "gia_da_xanh_reu",
    "xanh reu":  "gia_da_xanh_reu",
    "xanh":      "gia_da_xanh_reu",
    "xám":       "gia_da_xam_bd",
    "xam":       "gia_da_xam_bd",
    "granite":   "gia_da_grn_an_do",
    "ấn độ":     "gia_da_grn_an_do",
    "an do":     "gia_da_grn_an_do",
    "ấn":        "gia_da_grn_an_do",
}

_THE_LOAI_MAP = {
    "mộ": "Mộ",
    "mo": "Mộ",
    "mộ đơn": "Mộ",
    "mo don": "Mộ",
    "mộ đôi": "Mộ",
    "mo doi": "Mộ",
    "mộ tròn": "Mộ",
    "mo tron": "Mộ",
    "long đình": "Long đình",
    "long dinh": "Long đình",
    "lăng": "Long đình",
    "cuốn thư": "Cuốn thư",
    "cuon thu": "Cuốn thư",
    "cổng": "Cổng",
    "cong": "Cổng",
    "tam sơn": "Tam sơn",
    "tam son": "Tam sơn",
    "hàng rào": "Hàng rào",
    "hang rao": "Hàng rào",
    "lan can": "Hàng rào",
}


# ---------------------------------------------------------------------------
# Slot parsers
# ---------------------------------------------------------------------------

def _parse_budget(budget_text: str | None) -> int | None:
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


def _parse_size_mm(raw: str | None) -> int | None:
    """Parse '1200mm' or '1.2m' or '120cm' → int mm."""
    if not raw:
        return None
    raw = raw.lower().strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*m(?:m)?\b", raw)
    if m:
        val = float(m.group(1))
        return int(val * 1000) if "." in m.group(1) else int(val)
    m = re.search(r"(\d+(?:\.\d+)?)\s*cm\b", raw)
    if m:
        return int(float(m.group(1)) * 10)
    m = re.search(r"(\d+)", raw)
    if m:
        return int(m.group(1))
    return None


def _resolve_the_loai(slots: dict) -> str | None:
    raw = slots.get("product_type") or slots.get("project_type")
    if not raw:
        return None
    key = raw.lower().strip()
    return _THE_LOAI_MAP.get(key)  # None if not in map → no filter


def _resolve_stone_col(slots: dict) -> str | None:
    """Map stone_type slot to the DB price column for that stone variety."""
    raw = slots.get("stone_type")
    if not raw:
        return None
    return _STONE_COL_MAP.get(raw.lower().strip())


def _extract_keywords(query: str) -> list[str]:
    words = re.sub(r"[.,?!]", " ", query.lower()).split()
    return [w for w in words if len(w) >= 2 and w not in _STOPWORDS][:4]


# ---------------------------------------------------------------------------
# SQL filters builder
# ---------------------------------------------------------------------------

def _apply_sql_filters(
    q, the_loai: str | None, budget: int | None, sizes: dict, stone_col: str | None = None
) -> object:
    if the_loai:
        q = q.ilike("the_loai", f"%{the_loai}%")

    if stone_col:
        # Only return products available in this stone variety
        q = q.not_.is_(stone_col, "null")

    if budget:
        price_cols = (
            [stone_col] if stone_col
            else ["gia_da_xanh_den", "gia_da_xanh_reu", "gia_da_xam_bd", "gia_da_grn_an_do"]
        )
        q = q.or_(",".join(f"{c}.lte.{budget}" for c in price_cols))

    if sizes.get("chieu_dai_max"):
        q = q.lte("chieu_dai_mm", sizes["chieu_dai_max"])
    if sizes.get("chieu_cao_max"):
        q = q.lte("chieu_cao_mm", sizes["chieu_cao_max"])
    if sizes.get("chieu_rong_max"):
        q = q.lte("chieu_rong_mm", sizes["chieu_rong_max"])

    return q


# ---------------------------------------------------------------------------
# Search layers
# ---------------------------------------------------------------------------

_SIMILARITY_THRESHOLD = 0.30


async def _semantic_search(
    query: str,
    the_loai: str | None,
    budget: int | None,
    n: int,
) -> list[dict]:
    """pgvector semantic search via match_products RPC. Filters by similarity threshold."""
    client = get_client()
    try:
        vector = await embed(query)
        params: dict = {
            "query_embedding": vector,
            "match_count": n * 2,
        }
        if the_loai:
            params["filter_the_loai"] = the_loai
        if budget:
            params["filter_price_max"] = budget
        result = await asyncio.to_thread(
            lambda: client.rpc("match_products", params).execute()
        )
        rows = result.data or []
        return [r for r in rows if (r.get("similarity") or 0) >= _SIMILARITY_THRESHOLD]
    except Exception:
        logger.exception("semantic_search failed query={!r}", query[:50])
        return []


def _ilike_search(
    keywords: list[str],
    the_loai: str | None,
    budget: int | None,
    sizes: dict,
    n: int,
    stone_col: str | None = None,
) -> list[dict]:
    """Single query: OR across all keywords on search_text (pg_trgm accelerated)."""
    client = get_client()
    try:
        q = client.table("products").select(_SELECT_COLS)
        if keywords:
            q = q.or_(",".join(f"search_text.ilike.%{kw}%" for kw in keywords))
        q = _apply_sql_filters(q, the_loai, budget, sizes, stone_col)
        result = q.order("ban_chay", desc=True).limit(n).execute()
        return result.data or []
    except Exception:
        logger.exception("ilike_search failed keywords={}", keywords)
        return []


def _filter_by_size(products: list[dict], sizes: dict) -> list[dict]:
    """Post-filter semantic results by dimension constraints."""
    out = []
    for p in products:
        if sizes.get("chieu_dai_max"):
            v = p.get("chieu_dai_mm")
            if v and v > sizes["chieu_dai_max"]:
                continue
        if sizes.get("chieu_cao_max"):
            v = p.get("chieu_cao_mm")
            if v and v > sizes["chieu_cao_max"]:
                continue
        if sizes.get("chieu_rong_max"):
            v = p.get("chieu_rong_mm")
            if v and v > sizes["chieu_rong_max"]:
                continue
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def search_products(query: str, slots: dict, n: int = 6) -> list[dict]:
    """
    Hybrid search:
      1. Semantic (pgvector) → post-filter by stone type and dimensions
      2. ILIKE fallback (pg_trgm on search_text)
      3. Bestseller fallback with SQL filters only
    """
    the_loai  = _resolve_the_loai(slots)
    stone_col = _resolve_stone_col(slots)
    budget    = _parse_budget(slots.get("budget"))

    # Augment query with stone type for better semantic matching
    semantic_query = query
    if slots.get("stone_type"):
        semantic_query = f"{slots['stone_type']} {query}"

    sizes = {
        "chieu_dai_max":  _parse_size_mm(slots.get("chieu_dai")),
        "chieu_cao_max":  _parse_size_mm(slots.get("chieu_cao")),
        "chieu_rong_max": _parse_size_mm(slots.get("chieu_rong")),
    }
    sizes = {k: v for k, v in sizes.items() if v}  # drop None

    keywords = _extract_keywords(query)

    # Layer 1: Semantic search — skip when query has fewer than 2 meaningful keywords
    if len(keywords) >= 2:
        products = await _semantic_search(semantic_query, the_loai, budget, n)
        # Post-filter by stone type: only keep rows with a price for that stone
        if products and stone_col:
            products = [p for p in products if p.get(stone_col)]
        if products and sizes:
            products = _filter_by_size(products, sizes)
    else:
        products = []

    # Layer 2: ILIKE fallback — single OR query across all keywords
    if not products:
        products = await asyncio.to_thread(
            _ilike_search, keywords, the_loai, budget, sizes, n, stone_col
        )

    # Layer 3: bestseller fallback (SQL filters only, no keyword constraint)
    if not products:
        products = await asyncio.to_thread(
            _ilike_search, [], the_loai, budget, sizes, n, stone_col
        )

    logger.info(
        "search found={} query={!r} the_loai={} stone_col={} budget={} sizes={}",
        len(products), query[:50], the_loai, stone_col, budget, sizes,
    )
    return products


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
        name  = p.get("ten_sp", "")
        size  = p.get("kich_thuoc", "")
        price = _best_price(p)
        price_str = f"{price:,}₫" if price else "Liên hệ"
        desc  = p.get("mo_ta") or p.get("ghi_chu") or ""
        desc_str = (desc[:80] + "…") if len(desc) > 80 else desc

        line = f"{i}. {name}"
        if size:
            first_line = size.split("\n")[0]
            line += f" ({first_line})"
        line += f"\n   Giá từ {price_str}"
        if desc_str:
            line += f"\n   {desc_str}"
        lines.append(line)

    if len(products) > 3:
        lines.append(f"\n...và {len(products) - 3} sản phẩm khác. Anh/chị muốn xem thêm không ạ?")

    return "\n".join(lines)


def format_products_for_llm(products: list[dict]) -> str:
    if not products:
        return "Không tìm thấy sản phẩm phù hợp."
    lines = ["Sản phẩm gợi ý từ kho hàng Hồn Đá:"]
    for p in products[:5]:
        price = _best_price(p)
        price_str = f"{price:,}đ" if price else "Liên hệ"
        dai  = p.get("chieu_dai_mm")
        cao  = p.get("chieu_cao_mm")
        rong = p.get("chieu_rong_mm")
        dim_str = ""
        if dai or cao or rong:
            parts = []
            if dai:  parts.append(f"dài {dai}mm")
            if rong: parts.append(f"rộng {rong}mm")
            if cao:  parts.append(f"cao {cao}mm")
            dim_str = " | " + ", ".join(parts)
        mo_ta = p.get("mo_ta") or ""
        line = (
            f"- {p['ten_sp']} | {p.get('the_loai', '')}{dim_str} "
            f"| Từ {price_str} | {mo_ta[:80]}"
        )
        lines.append(line)
    return "\n".join(lines)
