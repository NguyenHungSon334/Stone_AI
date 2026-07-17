"""
Tool: tra sản phẩm theo tầm giá. Dùng cho bot khi khách hỏi "tầm 100 triệu đổ xuống có gì".

Đọc Danh_Muc_San_Pham.csv (6 cột giá theo loại đá). Lọc theo khoảng giá, loại đá, danh mục;
sắp xếp giá tăng dần. Bot gọi qua Bash (cần BOT_ALLOWED_TOOLS có Bash).

Ví dụ:
  python bot_tools/find_by_price.py --max 100tr
  python bot_tools/find_by_price.py --max 200tr --min 100tr --stone "xanh rêu"
  python bot_tools/find_by_price.py --max 150tr --category "Trường Tồn" --limit 20
  python bot_tools/find_by_price.py --selftest
"""
import argparse
import csv
import re
import sys
from pathlib import Path

CSV = Path(__file__).resolve().parent.parent / "Document_ChatBot_Mess" / "Danh_Muc_San_Pham.csv"

_MONEY = [("ty", 1e9), ("tỷ", 1e9), ("triệu", 1e6), ("tr", 1e6), ("củ", 1e6),
          ("nghìn", 1e3), ("ngàn", 1e3), ("k", 1e3)]


def parse_money(s: str) -> float:
    """'100tr' -> 1e8, '1.2 tỷ' -> 1.2e9, '235756000' -> 235756000. Trả -1 nếu không đọc được."""
    s = str(s).strip().lower().replace(",", ".")
    if not s:
        return -1.0
    for suf, mul in _MONEY:
        if s.endswith(suf):
            num = s[: -len(suf)].strip()
            try:
                return float(num) * mul
            except ValueError:
                return -1.0
    digits = re.sub(r"[^\d.]", "", s)
    try:
        return float(digits) if digits else -1.0
    except ValueError:
        return -1.0


def fmt_money(v: float) -> str:
    if v >= 1e9:
        return f"{v / 1e9:.2f} tỷ".replace(".00", "")
    return f"{v / 1e6:.1f}tr".replace(".0tr", "tr")


def load_rows():
    with open(CSV, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    stone_cols = [i for i, h in enumerate(header) if h.strip().lower().startswith("đá")]
    return header, stone_cols, rows[1:]


def search(max_price, min_price=0.0, stone=None, category=None, limit=15):
    header, stone_cols, data = load_rows()
    cols = stone_cols
    if stone:
        s = stone.strip().lower()
        cols = [i for i in stone_cols if s in header[i].strip().lower()]
        if not cols:
            return []  # loại đá không khớp cột nào
    out = []
    for r in data:
        if not r or not r[0].strip():
            continue
        if category and category.strip().lower() not in (r[3] if len(r) > 3 else "").lower():
            continue
        # giá ứng viên: nếu chọn loại đá -> giá loại đó; không thì loại rẻ nhất trong tầm.
        best = None
        for i in cols:
            v = parse_money(r[i]) if i < len(r) else -1.0
            if v <= 0:
                continue
            if min_price <= v <= max_price and (best is None or v < best[0]):
                best = (v, header[i].strip())
        if best:
            out.append((best[0], best[1], r))
    out.sort(key=lambda x: x[0])
    return out[:limit]


def rows_by_ids(ids) -> list:
    """Lấy sản phẩm theo danh sách mã (khớp chính xác). Trả cùng shape với search(): (giá_rẻ_nhất, tên_đá, row)."""
    header, stone_cols, data = load_rows()
    want = {str(i).strip().upper() for i in ids}
    out = []
    for r in data:
        if not r or r[0].strip().upper() not in want:
            continue
        best = None
        for i in stone_cols:
            v = parse_money(r[i]) if i < len(r) else -1.0
            if v > 0 and (best is None or v < best[0]):
                best = (v, header[i].strip())
        out.append((best[0] if best else 0.0, best[1] if best else "", r))
    return out


def render(results) -> str:
    if not results:
        return "Không có sản phẩm nào trong tầm giá này."
    lines = [f"Tìm thấy {len(results)} sản phẩm (giá tăng dần):"]
    for price, stone_name, r in results:
        ma, ten = r[0], (r[1] if len(r) > 1 else "")
        dm = r[3] if len(r) > 3 else ""
        lines.append(f"{ma} | {ten} | {dm} | {fmt_money(price)} ({stone_name})")
    return "\n".join(lines)


def _selftest():
    assert parse_money("100tr") == 1e8
    assert parse_money("1.2 tỷ") == 1.2e9
    assert parse_money("235756000") == 235756000
    assert parse_money("bậy") == -1.0
    assert fmt_money(235756000).endswith("tr")
    res = search(max_price=1e8)  # <=100tr
    assert all(p <= 1e8 for p, _, _ in res), "lọc max sai"
    assert res == sorted(res, key=lambda x: x[0]), "chưa sort tăng dần"
    print(f"selftest OK - {len(res)} sp <=100tr, rẻ nhất {fmt_money(res[0][0])}" if res else "selftest OK - rỗng")


def main():
    ap = argparse.ArgumentParser(description="Tra sản phẩm đá mỹ nghệ theo tầm giá.")
    ap.add_argument("--max", help="Giá tối đa, vd 100tr / 1.5 tỷ / 100000000")
    ap.add_argument("--min", default="0", help="Giá tối thiểu (mặc định 0)")
    ap.add_argument("--stone", help="Loại đá, vd 'xanh rêu', 'GRN', 'trắng Yên Bái'")
    ap.add_argument("--category", help="Danh mục, vd 'Trường Tồn'")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        _selftest()
        return
    if not a.max:
        ap.error("cần --max (giá tối đa)")
    mx, mn = parse_money(a.max), parse_money(a.min)
    if mx <= 0:
        ap.error(f"không đọc được --max '{a.max}'")
    print(render(search(mx, max(mn, 0.0), a.stone, a.category, a.limit)))


if __name__ == "__main__":
    sys.exit(main())
