"""
Product tools backed by danh_sach_san_pham.csv.

Public API
----------
search_product(keyword, n)          – search by name / product code
filter_product(n, **criteria)       – filter by any field combination
get_product_detail(ma_sp)           – full detail of one product
get_price(ma_sp, loai_da)           – price(s) by stone type
get_media(ma_sp, loai)              – image / video URLs
search_products(query, slots, n)    – legacy: kept for orchestrator compat
format_results(products)            – format for end-user display
format_products_for_llm(products)   – format for LLM context
"""
from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path
from typing import Any

from loguru import logger

_CSV_PATH = Path(__file__).parent.parent.parent / "danh_sach_san_pham.csv"

# ---------------------------------------------------------------------------
# CSV → internal model
# ---------------------------------------------------------------------------

_COL = {
    "id":            "record_id",
    "ma_sp":         "Mã Sản Phẩm",
    "ten_sp":        "Tên sản phẩm",
    "ban_chay":      "Bán chạy",
    "anh":           "Ảnh",
    "anh_bao_gia":   "Ảnh báo giá(1 ảnh rõ sản phẩm)",
    "sp_uu_tien":    "sp ưu tiên",
    "nhom_cong_viec":"Nhóm công việc",
    "media":         "Media",
    "mo_ta":         "Mô tả",
    "danh_muc":      "Danh mục",
    "the_loai":      "Thể Loại",
    "don_vi":        "Đơn vị",
    "quy_cach":      "Quy CáchGhép SP",
    "kich_thuoc":    "Kích thước",
    "khoi_luong_m3": "Khối lượng (m3)",
    "trong_luong_tan":"Trọng lượng (tấn)",
    "gia_xanh_den":  "Đá xanh đen",
    "gia_xanh_reu":  "Đá xanh rêu",
    "gia_xam_bd":    "Đá xám BĐ",
    "gia_grn_an_do": "Đá GRN đen Ấn Độ",
    "ghi_chu":       "Ghi chú",
    "video":         "Video",
    "link_anh":      "Link ảnh",
    "link_anh_ma":   "Link ảnh có mã",
}

# stone alias → internal key
_STONE_KEY: dict[str, str] = {
    "xanh đen":  "gia_xanh_den",
    "xanh den":  "gia_xanh_den",
    "đen":       "gia_xanh_den",
    "xanh rêu":  "gia_xanh_reu",
    "xanh reu":  "gia_xanh_reu",
    "xanh":      "gia_xanh_reu",
    "xám":       "gia_xam_bd",
    "xam":       "gia_xam_bd",
    "granite":   "gia_grn_an_do",
    "ấn độ":     "gia_grn_an_do",
    "an do":     "gia_grn_an_do",
    "ấn":        "gia_grn_an_do",
    "grn":       "gia_grn_an_do",
}

_PRICE_KEYS = ["gia_xanh_den", "gia_xanh_reu", "gia_xam_bd", "gia_grn_an_do"]
_PRICE_LABELS = {
    "gia_xanh_den":  "Đá xanh đen",
    "gia_xanh_reu":  "Đá xanh rêu",
    "gia_xam_bd":    "Đá xám BĐ",
    "gia_grn_an_do": "Đá GRN đen Ấn Độ",
}

_THE_LOAI_MAP = {
    "mộ": "Mộ", "mo": "Mộ", "mộ đơn": "Mộ", "mo don": "Mộ",
    "mộ đôi": "Mộ", "mo doi": "Mộ", "mộ tròn": "Mộ", "mo tron": "Mộ",
    "long đình": "Long đình", "long dinh": "Long đình", "lăng": "Long đình",
    "cuốn thư": "Cuốn thư", "cuon thu": "Cuốn thư",
    "cổng": "Cổng", "cong": "Cổng",
    "tam sơn": "Tam sơn", "tam son": "Tam sơn",
    "hàng rào": "Hàng rào", "hang rao": "Hàng rào", "lan can": "Hàng rào",
}

def _norm(s: str) -> str:
    """Lowercase + strip Vietnamese diacritics (incl. đ→d) for accent-insensitive matching."""
    s = s.lower().replace("đ", "d").replace("Đ", "d")
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()


_STOPWORDS = {
    "cho", "tôi", "em", "anh", "chị", "bác", "về", "của", "và", "hoặc",
    "có", "không", "một", "cái", "bộ", "hỏi", "muốn", "xem", "cần",
    "được", "thì", "là", "ở", "tại", "với", "các", "những", "này",
    "loại", "sản", "phẩm", "đá", "hàng", "giá", "bao", "nhiêu",
}


def _parse_int(val: str) -> int | None:
    if not val or not val.strip():
        return None
    try:
        return int(float(val.strip().replace(",", ".")))
    except (ValueError, TypeError):
        return None


def _parse_dimensions(kich_thuoc: str) -> dict[str, int]:
    if not kich_thuoc:
        return {}
    dims: dict[str, int] = {}
    m = re.search(r"chiều dài[^:]*:\s*(\d[\d.]*)\s*mm", kich_thuoc, re.IGNORECASE)
    if m:
        dims["chieu_dai_mm"] = int(m.group(1).replace(".", ""))
    m = re.search(r"chiều cao[^:]*:\s*(\d[\d.]*)\s*mm", kich_thuoc, re.IGNORECASE)
    if m:
        dims["chieu_cao_mm"] = int(m.group(1).replace(".", ""))
    m = re.search(r"chiều rộng[^:]*:\s*(\d[\d.]*)\s*mm", kich_thuoc, re.IGNORECASE)
    if m:
        dims["chieu_rong_mm"] = int(m.group(1).replace(".", ""))
    return dims


def _split_urls(raw: str) -> list[str]:
    """Split URLs separated by | or newlines."""
    urls: list[str] = []
    for part in re.split(r"[|\n]", raw):
        u = part.strip()
        if u:
            urls.append(u)
    return urls


def _load_products() -> list[dict]:
    products: list[dict] = []
    try:
        with open(_CSV_PATH, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                kt = row.get("Kích thước", "") or ""
                dims = _parse_dimensions(kt)
                p: dict = {
                    "id":             row.get("record_id", ""),
                    "ma_sp":          row.get("Mã Sản Phẩm", ""),
                    "ten_sp":         row.get("Tên sản phẩm", ""),
                    "ban_chay":       bool((row.get("Bán chạy") or "").strip()),
                    "anh":            _split_urls(row.get("Ảnh", "") or ""),
                    "anh_bao_gia":    (row.get("Ảnh báo giá(1 ảnh rõ sản phẩm)") or "").strip(),
                    "sp_uu_tien":     (row.get("sp ưu tiên") or "").strip(),
                    "nhom_cong_viec": (row.get("Nhóm công việc") or "").strip(),
                    "media":          (row.get("Media") or "").strip(),
                    "mo_ta":          row.get("Mô tả", "") or "",
                    "danh_muc":       row.get("Danh mục", "") or "",
                    "the_loai":       row.get("Thể Loại", "") or "",
                    "don_vi":         row.get("Đơn vị", "") or "",
                    "quy_cach":       row.get("Quy CáchGhép SP", "") or "",
                    "kich_thuoc":     kt,
                    "khoi_luong_m3":  row.get("Khối lượng (m3)", "") or "",
                    "trong_luong_tan": row.get("Trọng lượng (tấn)", "") or "",
                    "gia_xanh_den":   _parse_int(row.get("Đá xanh đen", "")),
                    "gia_xanh_reu":   _parse_int(row.get("Đá xanh rêu", "")),
                    "gia_xam_bd":     _parse_int(row.get("Đá xám BĐ", "")),
                    "gia_grn_an_do":  _parse_int(row.get("Đá GRN đen Ấn Độ", "")),
                    "ghi_chu":        row.get("Ghi chú", "") or "",
                    "video":          _split_urls(row.get("Video", "") or ""),
                    "link_anh":       _split_urls(row.get("Link ảnh", "") or ""),
                    "link_anh_ma":    _split_urls(row.get("Link ảnh có mã", "") or ""),
                    **dims,
                }
                products.append(p)
        logger.info("Loaded {} products from CSV", len(products))
    except Exception:
        logger.exception("Failed to load products from CSV: {}", _CSV_PATH)
    return products


_PRODUCTS: list[dict] = _load_products()
_BY_MA: dict[str, dict] = {p["ma_sp"].upper(): p for p in _PRODUCTS if p["ma_sp"]}


# ---------------------------------------------------------------------------
# search_product
# ---------------------------------------------------------------------------

def search_product(keyword: str, n: int = 10) -> list[dict]:
    """Search by keyword matched against name and product code."""
    kw = _norm(keyword)
    results = [
        p for p in _PRODUCTS
        if kw in _norm(p["ten_sp"]) or kw in _norm(p["ma_sp"])
    ]
    logger.info("search_product keyword={!r} found={}", keyword, len(results))
    return results[:n]


# ---------------------------------------------------------------------------
# filter_product
# ---------------------------------------------------------------------------

def filter_product(n: int = 20, **criteria: Any) -> list[dict]:
    """
    Filter products by any field combination.

    Supported criteria keys (all optional):
        danh_muc, the_loai, don_vi, ban_chay (bool),
        ma_sp, ten_sp, mo_ta, ghi_chu, quy_cach,
        sp_uu_tien, nhom_cong_viec,
        gia_xanh_den_max, gia_xanh_reu_max,
        gia_xam_bd_max, gia_grn_an_do_max,
        gia_max (applies to all price cols),
        chieu_dai_max, chieu_cao_max, chieu_rong_max (mm)

    String comparisons are case-insensitive substring matches.
    """
    results = list(_PRODUCTS)

    for key, val in criteria.items():
        if val is None:
            continue

        # boolean fields
        if key == "ban_chay":
            results = [p for p in results if bool(p.get("ban_chay")) == bool(val)]

        # price ceiling: any stone
        elif key == "gia_max":
            ceiling = int(val)
            results = [
                p for p in results
                if any((p.get(c) or 0) and p[c] <= ceiling for c in _PRICE_KEYS)
            ]

        # price ceiling: specific stone
        elif key in ("gia_xanh_den_max", "gia_xanh_reu_max", "gia_xam_bd_max", "gia_grn_an_do_max"):
            col = key[:-4]  # strip '_max'
            ceiling = int(val)
            results = [p for p in results if (p.get(col) or 0) and p[col] <= ceiling]

        # dimension ceilings
        elif key == "chieu_dai_max":
            results = [p for p in results if not p.get("chieu_dai_mm") or p["chieu_dai_mm"] <= int(val)]
        elif key == "chieu_cao_max":
            results = [p for p in results if not p.get("chieu_cao_mm") or p["chieu_cao_mm"] <= int(val)]
        elif key == "chieu_rong_max":
            results = [p for p in results if not p.get("chieu_rong_mm") or p["chieu_rong_mm"] <= int(val)]

        # string fields: accent-insensitive substring
        elif key in ("danh_muc", "the_loai", "don_vi", "ma_sp", "ten_sp",
                     "mo_ta", "ghi_chu", "quy_cach", "sp_uu_tien", "nhom_cong_viec"):
            needle = _norm(str(val))
            results = [p for p in results if needle in _norm(p.get(key) or "")]

        else:
            logger.warning("filter_product: unknown criterion '{}' ignored", key)

    # bestsellers first
    results.sort(key=lambda p: not p.get("ban_chay"))
    logger.info("filter_product criteria={} found={}", criteria, len(results))
    return results[:n]


# ---------------------------------------------------------------------------
# get_product_detail
# ---------------------------------------------------------------------------

def get_product_detail(ma_sp: str) -> dict | None:
    """Return full product dict for a given product code, or None."""
    return _BY_MA.get(ma_sp.upper().strip())


# ---------------------------------------------------------------------------
# get_price
# ---------------------------------------------------------------------------

def get_price(ma_sp: str, loai_da: str | None = None) -> dict[str, int | None]:
    """
    Return price(s) for a product.

    loai_da: optional stone alias (e.g. 'xanh đen').
    Returns dict mapping stone label → price (None if unavailable).
    """
    p = _BY_MA.get(ma_sp.upper().strip())
    if not p:
        return {}

    if loai_da:
        key = _STONE_KEY.get(loai_da.lower().strip())
        if key:
            return {_PRICE_LABELS[key]: p.get(key)}
        return {}

    return {_PRICE_LABELS[k]: p.get(k) for k in _PRICE_KEYS}


# ---------------------------------------------------------------------------
# get_media
# ---------------------------------------------------------------------------

def get_media(ma_sp: str, loai: str = "tất cả") -> dict[str, Any]:
    """
    Return media URLs for a product.

    loai: 'ảnh' | 'video' | 'tất cả' (default)
    """
    p = _BY_MA.get(ma_sp.upper().strip())
    if not p:
        return {}

    loai = loai.lower().strip()
    result: dict[str, Any] = {}

    if loai in ("ảnh", "anh", "tất cả", "tat ca", "all", ""):
        result["anh"] = p.get("anh", [])
        result["anh_bao_gia"] = p.get("anh_bao_gia", "")  # Lark URL — may expire
        result["link_anh"] = p.get("link_anh", [])
        result["link_anh_ma"] = p.get("link_anh_ma", [])  # Cloudinary — reliable

    if loai in ("video", "tất cả", "tat ca", "all", ""):
        result["video"] = p.get("video", [])

    return result


# ---------------------------------------------------------------------------
# Legacy: search_products (slots-based, used by orchestrator)
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


def _extract_keywords(query: str) -> list[str]:
    words = re.sub(r"[.,?!]", " ", query.lower()).split()
    return [w for w in words if len(w) >= 2 and w not in _STOPWORDS][:4]


async def search_products(query: str, slots: dict, n: int = 6) -> list[dict]:
    """Legacy orchestrator entry-point. Delegates to filter_product + keyword scoring."""
    raw_type = slots.get("product_type") or slots.get("project_type") or ""
    the_loai = _THE_LOAI_MAP.get(raw_type.lower().strip()) if raw_type else None

    raw_stone = slots.get("stone_type") or ""
    stone_key = _STONE_KEY.get(raw_stone.lower().strip()) if raw_stone else None

    budget = _parse_budget(slots.get("budget"))

    criteria: dict[str, Any] = {}
    if the_loai:
        criteria["the_loai"] = the_loai
    if budget:
        if stone_key:
            criteria[f"{stone_key}_max"] = budget
        else:
            criteria["gia_max"] = budget

    for slot_key, dim_key in [("chieu_dai", "chieu_dai_max"), ("chieu_cao", "chieu_cao_max"), ("chieu_rong", "chieu_rong_max")]:
        v = _parse_size_mm(slots.get(slot_key))
        if v:
            criteria[dim_key] = v

    results = filter_product(n=50, **criteria)

    if stone_key:
        results = [p for p in results if p.get(stone_key)]

    keywords = _extract_keywords(query)

    def _score(p: dict) -> int:
        text = " ".join(filter(None, [
            p.get("ten_sp"), p.get("ma_sp"), p.get("mo_ta"),
            p.get("ghi_chu"), p.get("the_loai"), p.get("danh_muc"),
        ])).lower()
        return sum(1 for kw in keywords if kw in text)

    results.sort(key=lambda p: (-_score(p), not p.get("ban_chay")))
    return results[:n]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _best_price(p: dict) -> int | None:
    valid = [p[k] for k in _PRICE_KEYS if p.get(k)]
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
            first_line = size.split("\n")[0].lstrip("- ").strip()
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
        parts = []
        if dai:  parts.append(f"dài {dai}mm")
        if rong: parts.append(f"rộng {rong}mm")
        if cao:  parts.append(f"cao {cao}mm")
        dim_str = (" | " + ", ".join(parts)) if parts else ""
        mo_ta = p.get("mo_ta") or ""
        lines.append(
            f"- {p['ten_sp']} ({p.get('ma_sp', '')}) | {p.get('the_loai', '')}{dim_str} "
            f"| Từ {price_str} | {mo_ta[:80]}"
        )
    return "\n".join(lines)
