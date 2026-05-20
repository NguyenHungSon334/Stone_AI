"""
Hybrid product search: pgvector (semantic) + SQL (price, dimensions, the_loai).

Strategy:
  1. Extract hard SQL filters from slots (the_loai, budget, dimensions)
  2. Semantic query via pgvector (match_products RPC)
  3. Fallback: ILIKE on search_text (pg_trgm index)
  4. Final fallback: bestsellers with SQL filters only
"""
from __future__ import annotations

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


def _extract_keywords(query: str) -> list[str]:
    words = re.sub(r"[.,?!]", " ", query.lower()).split()
    return [w for w in words if len(w) >= 2 and w not in _STOPWORDS][:4]


# ---------------------------------------------------------------------------
# SQL filters builder
# ---------------------------------------------------------------------------

def _apply_sql_filters(q, the_loai: str | None, budget: int | None, sizes: dict) -> object:
    if the_loai:
        q = q.ilike("the_loai", f"%{the_loai}%")

    if budget:
        price_cols = [
            "gia_da_xanh_den", "gia_da_xanh_reu",
            "gia_da_xam_bd", "gia_da_grn_an_do",
        ]
        # at least one price must be ≤ budget
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

async def _semantic_search(
    query: str,
    the_loai: str | None,
    budget: int | None,
    n: int,
) -> list[dict]:
    """pgvector semantic search via match_products RPC."""
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
        result = client.rpc("match_products", params).execute()
        return result.data or []
    except Exception:
        logger.exception("semantic_search failed query={!r}", query[:50])
        return []


def _ilike_search(
    keywords: list[str],
    the_loai: str | None,
    budget: int | None,
    sizes: dict,
    n: int,
) -> list[dict]:
    """Keyword ILIKE on search_text column (pg_trgm accelerated)."""
    client = get_client()
    try:
        q = client.table("products").select(_SELECT_COLS)
        for kw in keywords:
            q = q.ilike("search_text", f"%{kw}%")
        q = _apply_sql_filters(q, the_loai, budget, sizes)
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
      1. Semantic (pgvector) → post-filter by dimensions
      2. ILIKE fallback (pg_trgm on search_text)
      3. Bestseller fallback with SQL filters only
    """
    the_loai = _resolve_the_loai(slots)
    budget   = _parse_budget(slots.get("budget"))

    sizes = {
        "chieu_dai_max":  _parse_size_mm(slots.get("chieu_dai")),
        "chieu_cao_max":  _parse_size_mm(slots.get("chieu_cao")),
        "chieu_rong_max": _parse_size_mm(slots.get("chieu_rong")),
    }
    sizes = {k: v for k, v in sizes.items() if v}  # drop None

    # Layer 1: Semantic search
    products = await _semantic_search(query, the_loai, budget, n)
    if products and sizes:
        products = _filter_by_size(products, sizes)

    # Layer 2: ILIKE fallback
    if not products:
        keywords = _extract_keywords(query)
        products = _ilike_search(keywords, the_loai, budget, sizes, n)

        # progressively drop keywords if still no results
        kws = list(keywords)
        while not products and len(kws) > 1:
            kws = kws[:-1]
            products = _ilike_search(kws, the_loai, budget, sizes, n)

    # Layer 3: bestseller fallback (SQL filters only)
    if not products:
        products = _ilike_search([], the_loai, budget, sizes, n)

    logger.info(
        "search found={} query={!r} the_loai={} budget={} sizes={}",
        len(products), query[:50], the_loai, budget, sizes,
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
            # Show first line of kich_thuoc only
            first_line = size.split("\n")[0]
            line += f" ({first_line})"
        line += f"\n   Gia tu {price_str}"
        if desc_str:
            line += f"\n   {desc_str}"
        lines.append(line)

    if len(products) > 3:
        lines.append(f"\n...va {len(products) - 3} san pham khac. Anh/chi muon xem them khong a?")

    return "\n".join(lines)


def format_products_for_llm(products: list[dict]) -> str:
    if not products:
        return "Khong tim thay san pham phu hop."
    lines = ["San pham goi y tu kho hang Hon Da:"]
    for p in products[:5]:
        price = _best_price(p)
        price_str = f"{price:,}d" if price else "Lien he"
        dai  = p.get("chieu_dai_mm")
        cao  = p.get("chieu_cao_mm")
        rong = p.get("chieu_rong_mm")
        dim_str = ""
        if dai or cao or rong:
            parts = []
            if dai:  parts.append(f"dai {dai}mm")
            if rong: parts.append(f"rong {rong}mm")
            if cao:  parts.append(f"cao {cao}mm")
            dim_str = " | " + ", ".join(parts)
        mo_ta = p.get("mo_ta") or ""
        line = (
            f"- {p['ten_sp']} | {p.get('the_loai', '')}{dim_str} "
            f"| Tu {price_str} | {mo_ta[:60]}"
        )
        lines.append(line)
    return "\n".join(lines)
