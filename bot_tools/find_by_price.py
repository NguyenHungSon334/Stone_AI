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
import unicodedata
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


def catalog_index() -> str:
    """Index GỌN cho system prompt: mã | tên | danh mục, 1 dòng/SP (~5.5k token).

    Trước đây nhồi NGUYÊN CSV (~26k token) vào prompt mỗi lượt -> prefill 33k, hàng đợi dài,
    dính 504 kể cả với tin 'Xin chào'. Nhưng bỏ sạch thì bot không biết có mẫu nào mà gọi tool.
    Index giữ đúng phần bot cần để BIẾT + CHỌN mã; giá/kích thước lấy qua suggest_products."""
    _, _, data = load_rows()
    lines = [f"{r[0].strip()} | {_cell(r, 1)} | {_cell(r, 3)}"
             for r in data if r and r[0].strip()]
    return "\n".join(lines)


# Thể loại -> tiền tố mã (theo mục 5 của persona). Mã nào không khớp 4 nhóm dưới đều là Mộ.
_KIND_BY_PREFIX = {"LD": "long dinh", "HR": "hang rao", "TQ": "cong", "TP": "cuon thu"}

def fold(s: str) -> str:
    """Bỏ dấu tiếng Việt. Khách Messenger gõ không dấu rất nhiều ('long dinh', 'hang rao')."""
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c)).replace("đ", "d")


# Khách nói gì thì coi là hỏi thể loại đó. Dò trên CẢ bản có dấu lẫn bản bỏ dấu.
# Bỏ dấu gây ĐỤNG NHAU: "công trình" -> "cong" đụng "cổng", "mô/mo hinh" đụng "mộ". Nên hai
# nhóm đó chỉ nhận dạng CÓ DẤU, hoặc dạng không dấu đi kèm từ định danh ("cong da", "mo da").
_KIND_WORDS = {
    "long dinh": r"long dinh|lau tho|am tho|lang tho|lang chung",
    "hang rao": r"hang rao|lan can",
    "cong": r"\bcổng\b|tam quan|cong da|cong nha tho",
    "cuon thu": r"cuon thu|binh phong",
    "mo": r"\bmộ\b|lăng mộ|lang mo|mo da|mo doi|mo don|mo tam son|ngoi mo",
}


def kind_of(ma: str) -> str:
    """Thể loại suy từ tiền tố mã. Không khớp nhóm nào -> Mộ (nhóm lớn nhất, 162 mẫu)."""
    alpha = re.match(r"[A-Za-z]+", str(ma).strip())
    return _KIND_BY_PREFIX.get(alpha.group(0).upper() if alpha else "", "mo")


def rows_by_kind(text: str, limit: int = 8) -> list:
    """Khách hỏi theo THỂ LOẠI ('tư vấn các mẫu long đình') -> vài mẫu tiêu biểu của loại đó.

    Công cụ không có tham số lọc thể loại, và bảng giá KHÔNG còn nằm trong prompt, nên nếu
    không tra sẵn ở đây thì bot chỉ còn cách tự gọi tool - mà mục tiêu 'xin số điện thoại'
    trong persona lấn át, nó né sang xin số và trả lời chay không có mẫu nào. Tra sẵn bằng code
    là đường DUY NHẤT chắc chắn có số thật. Ưu tiên hàng Bán chạy, cùng shape với search()."""
    low = (text or "").lower() + "\n" + fold(text)
    kinds = {k for k, pat in _KIND_WORDS.items() if re.search(pat, low)}
    if not kinds:
        return []
    header, stone_cols, data = load_rows()
    hit = [r for r in data if r and r[0].strip() and kind_of(r[0]) in kinds]
    hit.sort(key=lambda r: 0 if _cell(r, 2) else 1)      # Bán chạy lên trước, còn lại giữ thứ tự
    out = []
    for r in hit[:limit]:
        best = None
        for i in stone_cols:
            v = parse_money(r[i]) if i < len(r) else -1.0
            if v > 0 and (best is None or v < best[0]):
                best = (v, header[i].strip())
        out.append((best[0] if best else 0.0, best[1] if best else "", r))
    return out


def _cell(r, i) -> str:
    """Ô CSV đã gộp khoảng trắng. Vài ô (tên, ghi chú) có XUỐNG DÒNG bên trong -> index và
    render đều là định dạng 1-dòng-1-SP, để nguyên là vỡ dòng, bot đọc lệch mã."""
    if i >= len(r) or not r[i]:
        return ""
    return " ".join(r[i].split())


def _spec(header, r) -> str:
    """Kích thước + trọng lượng, bỏ ô trống. Bảng SP KHÔNG còn nằm trong prompt (chỉ còn
    index mã|tên|danh mục) nên đây là đường DUY NHẤT bot biết thông số - thiếu là bot bịa."""
    d, rg, c = _cell(r, 6), _cell(r, 7), _cell(r, 8)
    parts = []
    kt = " x ".join(x for x in (d, rg, c) if x)
    if kt:
        parts.append(f"KT(DxRxC) {kt}mm")
    if _cell(r, 9):
        parts.append(f"hộp thờ {_cell(r, 9)}mm")
    if _cell(r, 11):
        parts.append(f"{_cell(r, 11)} tấn")
    return ", ".join(parts)


def _prices(header, stone_cols, r) -> str:
    """Giá TỪNG loại đá. Trước đây bot đọc thẳng từ CSV trong prompt; giờ phải lấy qua đây.
    Ô <=0 = CHƯA CẬP NHẬT -> bỏ hẳn, không được in ra thành '0tr' (khách đọc thành miễn phí)."""
    out = []
    for i in stone_cols:
        v = parse_money(r[i]) if i < len(r) else -1.0
        if v > 0:
            out.append(f"{header[i].strip()} {fmt_money(v)}")
    return "; ".join(out)


def render(results) -> str:
    if not results:
        return "Không có sản phẩm nào trong tầm giá này."
    header, stone_cols, _ = load_rows()
    lines = [f"Tìm thấy {len(results)} sản phẩm (giá tăng dần):"]
    for price, stone_name, r in results:
        ma, ten = r[0], _cell(r, 1)
        dm = _cell(r, 3)
        head = f"{ma} | {ten} | {dm}"
        if _cell(r, 2):                      # cột 'Bán chạy' - persona ưu tiên giới thiệu trước
            head += " | BÁN CHẠY"
        if _spec(header, r):
            head += f" | {_spec(header, r)}"
        if _cell(r, 16):
            head += f" | ghi chú: {_cell(r, 16)}"
        lines.append(head)
        # Giá 0 trong bảng = CHƯA CẬP NHẬT, không phải miễn phí. Phải ghi ra chữ: search() lọc
        # theo tầm giá nên không bao giờ trả mã giá 0, nhưng rows_by_ids() (khách hỏi đích danh
        # mã) thì trả -> bot đọc "0tr" thành "0 đồng" cho khách là mất mặt + mất đơn.
        gia = _prices(header, stone_cols, r)
        lines.append(f"    giá: {gia}" if gia else "    giá: CHƯA CÓ GIÁ - chuyên gia báo riêng")
    return "\n".join(lines)


def _selftest():
    assert parse_money("100tr") == 1e8
    assert parse_money("1.2 tỷ") == 1.2e9
    assert parse_money("235756000") == 235756000
    assert parse_money("bậy") == -1.0
    assert fmt_money(235756000).endswith("tr")
    res = search(max_price=1e8)  # <=100tr
    assert all(p <= 1e8 for p, _, _ in res), "lọc max sai"
    assert all(p > 0 for p, _, _ in res), "mã chưa có giá (0) lọt vào kết quả tầm giá"
    assert res == sorted(res, key=lambda x: x[0]), "chưa sort tăng dần"
    # Mã chưa có giá: hỏi đích danh vẫn trả về, nhưng KHÔNG được hiện thành "0tr"
    _hdr, _cols, _data = load_rows()
    _zero = [r for r in _data if r and r[0].strip() and parse_money(r[12]) <= 0]
    if _zero:
        out = render(rows_by_ids([_zero[0][0].strip()]))
        assert "0tr" not in out, f"giá 0 lọt ra dạng số: {out}"
        # Mã trống SẠCH mọi cột đá mới được nói 'chưa có giá'; trống 1 cột thì vẫn phải báo giá
        # các loại đá còn lại, không được im.
        if not any(parse_money(_zero[0][i]) > 0 for i in _cols if i < len(_zero[0])):
            assert "CHƯA CÓ GIÁ" in out, f"mã không có giá nào mà không báo: {out}"

    # Bỏ CSV khỏi prompt -> tool là đường DUY NHẤT lấy thông số. Thiếu = bot bịa với khách.
    _spec_row = next((r for r in _data if r and r[0].strip() and _cell(r, 6) and _cell(r, 11)), None)
    if _spec_row:
        out = render(rows_by_ids([_spec_row[0].strip()]))
        assert "KT(DxRxC)" in out, f"render mất kích thước: {out}"
        assert "tấn" in out, f"render mất trọng lượng: {out}"
        assert out.count("giá:") == 1 and "Đá" in out, f"render mất giá theo loại đá: {out}"

    assert kind_of("LD02") == "long dinh" and kind_of("HR05") == "hang rao"
    assert kind_of("M01") == "mo" and kind_of("MCS3") == "mo" and kind_of("TQ01") == "cong"
    ld = rows_by_kind("Em muon tu van cac mau long dinh")
    assert ld and all(r[0].startswith("LD") for _, _, r in ld), f"loc long dinh sai: {ld[:2]}"
    assert len(ld) <= 8, "tra san qua nhieu dong -> phinh prompt"
    assert "KT(DxRxC)" in render(ld) and "Đá" in render(ld), "tra san thieu thong so/gia"
    # "một" chứa "mộ" -> khớp lỏng là mọi câu có chữ 'một' đều bị nhồi 8 ngôi mộ
    assert not rows_by_kind("Em muon hoi mot chut"), "'mot' bi nhan nham thanh 'mo'"
    assert not rows_by_kind("Xin chao"), "cau chao khong duoc tra san gi"
    assert all(r[0].startswith("HR") for _, _, r in rows_by_kind("bao gia hang rao da"))
    # Khach go CO DAU va KHONG DAU phai ra cung ket qua
    assert rows_by_kind("tư vấn mẫu long đình") == rows_by_kind("tu van mau long dinh")
    assert all(r[0].startswith("LD") for _, _, r in rows_by_kind("bao gia lang tho"))
    # Bo dau gay dung nhau: "cong trinh"/"mo hinh" KHONG duoc nhan thanh cong/mo
    assert not rows_by_kind("Nha em dang lam cong trinh o Nam Dinh"), "'cong trinh' -> cong"
    assert not rows_by_kind("cho em xem mo hinh 3d"), "'mo hinh' -> mo"
    assert all(r[0].startswith("TQ") for _, _, r in rows_by_kind("bao gia cổng đá"))
    assert rows_by_kind("gia mo da bao nhieu"), "'mo da' phai nhan la Mo"

    idx = catalog_index()
    assert len(idx.splitlines()) == len([r for r in _data if r and r[0].strip()]), "index thiếu mã"
    assert "179624000" not in idx, "index lọt giá (phải gọn, giá lấy qua tool)"
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
