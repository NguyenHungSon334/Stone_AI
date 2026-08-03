"""
Tool: tra sản phẩm đá mỹ nghệ. Bot gọi qua function-calling (khai báo trong brain.py).

Đọc Danh_Muc_San_Pham.csv. Lọc theo khoảng giá, loại đá, danh mục, THỂ LOẠI (cột của CSV)
và từ khoá tên; sắp xếp giá tăng dần.

Ví dụ:
  python bot_tools/find_by_price.py --max 100tr
  python bot_tools/find_by_price.py --max 200tr --min 100tr --stone "xanh rêu"
  python bot_tools/find_by_price.py --kind "Long đình" --max 150tr
  python bot_tools/find_by_price.py --q "mộ tròn"
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


_MA_HOP_LE = re.compile(r"^[A-Za-z]{1,4}\d{1,3}(\.\d+)?$")   # M01, LD03, TP02, M01.2


def _ma_hong(row) -> bool:
    """Dòng thiếu MÃ ở cột đầu -> mọi cột lệch một ô.

    Ca thật: 2 dòng bị dán thiếu mã nên cột 0 thành TÊN sản phẩm, 'Đơn vị' (ngôi) rơi vào cột
    Thể Loại -> kinds_available() sinh thêm thể loại ma tên 'ngôi' và AI coi đó là kind hợp lệ.
    Chặn ngay ở cửa đọc file: mã sai định dạng thì bỏ dòng, còn hơn để lệch ngầm."""
    return bool(row and row[0].strip()) and not _MA_HOP_LE.match(row[0].strip())


def load_rows():
    with open(CSV, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    stone_cols = [i for i, h in enumerate(header) if h.strip().lower().startswith("đá")]
    data, hong = [], []
    for r in rows[1:]:
        (hong if _ma_hong(r) else data).append(r)
    if hong:
        print(f"[bảng hàng] BỎ {len(hong)} dòng thiếu/sai MÃ sản phẩm: "
              + "; ".join(r[0].strip()[:40] for r in hong), file=sys.stderr)
    return header, stone_cols, data


def _stone_cols(header, stone_cols, stone: str | None):
    if not stone:
        return stone_cols
    s = fold(stone)
    aliases = {
        "granite": "grn",
        "granit": "grn",
        "g20": "grn",
        "an do": "grn",
    }
    s = aliases.get(s, s)
    return [i for i in stone_cols if s in fold(header[i])]


def _col(header, name: str) -> int:
    """Index cột theo TÊN header (bỏ dấu, khớp lỏng). -1 nếu CSV không có cột đó.

    Trước đây index viết cứng (r[3] danh mục, r[4] thể loại...). Chèn/đổi thứ tự cột trong CSV
    là lệch âm thầm: bot lấy 'Đơn vị' làm danh mục mà không ai biết."""
    want = fold(name)
    for i, h in enumerate(header):
        if want in fold(h):
            return i
    return -1


def kinds_available() -> list[str]:
    """Các Thể Loại đang có trong CSV. KHÔNG viết cứng trong code - thêm loại mới vào bảng
    hàng là bot dùng được ngay, khỏi sửa code."""
    header, _, data = load_rows()
    i = _col(header, "Thể Loại")
    if i < 0:
        return []
    seen = {_cell(r, i) for r in data if r and r[0].strip() and _cell(r, i)}
    return sorted(seen)


def _co_anh(header, r) -> bool:
    """Mã này có ảnh trong Lark Base? Cột 'Có ảnh' do bot_tools/sync_anh_flag.py ghi.
    Thiếu cột (bảng hàng cũ) -> coi như CÓ, để không tự dìm cả bảng xuống đáy."""
    i = _col(header, "Có ảnh")
    return True if i < 0 else _cell(r, i).strip().upper() != "FALSE"


def _uu_tien(header, r) -> tuple[int, int]:
    """Khoá sắp xếp ĐỨNG TRƯỚC giá: (bán chạy?, có ảnh?). 0 = lên trước.

    Mẫu bán chạy dễ chốt hơn; mẫu có ảnh thì khách xem được hình ngay trong tin - nói chay
    không ảnh là khách phải tự tưởng tượng, tỉ lệ rơi cao hơn hẳn."""
    i_bc = _col(header, "Bán chạy")
    return (0 if (i_bc >= 0 and _cell(r, i_bc)) else 1, 0 if _co_anh(header, r) else 1)


def search(max_price, min_price=0.0, stone=None, category=None, limit=15, kind=None, q=None,
           exclude_ids=None):
    """Lọc bảng hàng. max_price=None -> không giới hạn trần (dùng khi chỉ lọc kind/q).

    THỨ TỰ: bán chạy trước, có ảnh trước, rồi giá tăng dần (xem _uu_tien). Tool chỉ trả 3 mẫu
    cho bot nên sort thuần theo giá là mẫu bán chạy/có ảnh rơi khỏi top - khách nhận mẫu rẻ
    nhất thay vì mẫu dễ chốt và xem được hình.

    exclude_ids: bỏ các mã ĐÃ giới thiệu. Kết quả sort rồi cắt [:limit] nên cùng bộ lọc luôn ra
    đúng ngần ấy mã; khách đòi 'mẫu khác' mà không loại mã cũ là bot đưa lại y hệt."""
    header, stone_cols, data = load_rows()
    skip = {str(i).strip().upper() for i in (exclude_ids or [])}
    if max_price is None:
        max_price = float("inf")
    i_dm, i_tl, i_ten = _col(header, "Danh mục"), _col(header, "Thể Loại"), _col(header, "Tên sản phẩm")
    cols = _stone_cols(header, stone_cols, stone)
    if stone and not cols:
        return []  # loai da khong khop cot nao
    kind_f = fold(kind) if kind else ""
    # Khách/AI gõ "mộ tròn" -> mọi từ phải có mặt trong tên. Bỏ dấu cả hai phía: bảng hàng ghi
    # có dấu, khách Messenger gõ không dấu rất nhiều.
    q_words = [w for w in fold(q).split() if w] if q else []
    out = []
    for r in data:
        if not r or not r[0].strip():
            continue
        if skip and r[0].strip().upper() in skip:
            continue
        if category and category.strip().lower() not in _cell(r, i_dm).lower():
            continue
        if kind_f and kind_f not in fold(_cell(r, i_tl)):
            continue
        if q_words:
            hay = fold(_cell(r, i_ten) + " " + _cell(r, i_tl))
            if not all(w in hay for w in q_words):
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
            out.append((best[0], best[1], r, bool(stone)) if stone else (best[0], best[1], r))
    out.sort(key=lambda x: _uu_tien(header, x[2]) + (x[0],))
    return out[:limit]


def rows_by_ids(ids, stone=None) -> list:
    """Lấy sản phẩm theo danh sách mã (khớp chính xác). Trả cùng shape với search(): (giá_rẻ_nhất, tên_đá, row)."""
    header, stone_cols, data = load_rows()
    cols = _stone_cols(header, stone_cols, stone)
    if stone and not cols:
        return []
    want = {str(i).strip().upper() for i in ids}
    out = []
    for r in data:
        if not r or r[0].strip().upper() not in want:
            continue
        best = None
        for i in cols:
            v = parse_money(r[i]) if i < len(r) else -1.0
            if v > 0 and (best is None or v < best[0]):
                best = (v, header[i].strip())
        out.append((best[0] if best else 0.0, best[1] if best else "", r, bool(stone)) if stone else (best[0] if best else 0.0, best[1] if best else "", r))
    return out


def catalog_index() -> str:
    """Index GỌN cho system prompt: mã | tên | danh mục, 1 dòng/SP (~5.5k token).

    Trước đây nhồi NGUYÊN CSV (~26k token) vào prompt mỗi lượt -> prefill 33k, hàng đợi dài,
    dính 504 kể cả với tin 'Xin chào'. Nhưng bỏ sạch thì bot không biết có mẫu nào mà gọi tool.
    Index giữ đúng phần bot cần để BIẾT + CHỌN mã; giá/kích thước lấy qua suggest_products."""
    header, _, data = load_rows()
    i_ten, i_dm = _col(header, "Tên sản phẩm"), _col(header, "Danh mục")
    lines = [f"{r[0].strip()} | {_cell(r, i_ten)} | {_cell(r, i_dm)}"
             for r in data if r and r[0].strip()]
    return "\n".join(lines)


def fold(s: str) -> str:
    """Bỏ dấu tiếng Việt. Khách Messenger gõ không dấu rất nhiều ('long dinh', 'hang rao')."""
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c)).replace("đ", "d")


# Bản cũ có rows_by_kind(): regex từ khoá tự viết ("long dinh|lau tho|...") map sang tiền tố mã
# để tra sẵn 8 mẫu nhét vào prompt. Đã BỎ. Ba lý do:
#   - Gom sai: 162/292 mã rơi vào rổ "mo" mặc định, nên "mộ tròn", "mộ đôi", "mộ tam sơn" đều
#     trả về đúng 8 mẫu bán chạy giống hệt nhau.
#   - Sai nghiệp vụ: "lăng mộ" khớp rổ "mo" -> khách hỏi CẢ KHU lăng mộ, bot báo giá 1 ngôi mộ đôi.
#   - Câu tra sẵn dặn "dùng ngay, không cần gọi công cụ" -> AI bỏ luôn việc tự lọc danh sách.
# Nay AI tự lọc: tên sản phẩm trong danh sách ngữ cảnh đã chứa thể loại ("Long đình", "Mộ tròn",
# "Hàng rào 94"), và tool nhận thẳng kind/q để lọc theo cột CSV.


def _cell(r, i) -> str:
    """Ô CSV đã gộp khoảng trắng. Vài ô (tên, ghi chú) có XUỐNG DÒNG bên trong -> index và
    render đều là định dạng 1-dòng-1-SP, để nguyên là vỡ dòng, bot đọc lệch mã."""
    if i < 0 or i >= len(r) or not r[i]:   # i<0 = CSV thiếu cột đó; r[-1] sẽ trả nhầm cột cuối
        return ""
    return " ".join(r[i].split())


def _spec(header, r) -> str:
    """Kích thước + trọng lượng, bỏ ô trống. Bảng SP KHÔNG còn nằm trong prompt (chỉ còn
    index mã|tên|danh mục) nên đây là đường DUY NHẤT bot biết thông số - thiếu là bot bịa.

    Nhiều mã bỏ trống 3 cột dài/rộng/cao nhưng cột 'Kích thước' (chữ tự do, nhân viên gõ tay:
    'KT: 1970x970mm', 'ĐK 1470mm') vẫn có -> lấy nguyên văn ô đó thay vì im lặng. Bỏ thông số
    là bot phải tự bịa hoặc nói trống không, khách hỏi kích thước không có câu trả lời."""
    d, rg, c = (_cell(r, _col(header, n)) for n in ("Chieu_Dai", "Chieu_Rong", "Chieu_Cao"))
    hop, tan = _cell(r, _col(header, "Hop_Tho")), _cell(r, _col(header, "Trọng lượng"))
    parts = []
    if d and rg and c:
        parts.append(f"KT(DxRxC) {d} x {rg} x {c}mm")
    elif _cell(r, _col(header, "Kích thước")):
        # Thiếu chiều -> KHÔNG ghép nửa vời: "KT(DxRxC) 1470 x 1270mm" của mộ tròn là đường
        # kính x cao, đọc thành dài x rộng là sai hẳn. Ô chữ tự do ghi đúng ("DK1470xC1270").
        parts.append(_cell(r, _col(header, "Kích thước")))
    elif d or rg or c:
        parts.append("KT " + " x ".join(x for x in (d, rg, c) if x) + "mm")
    if hop:
        parts.append(f"hộp thờ {hop}mm")
    if tan:
        parts.append(f"{tan} tấn")
    return ", ".join(parts)


def _prices(header, stone_cols, r, only_stone: str | None = None) -> str:
    """Giá TỪNG loại đá. Trước đây bot đọc thẳng từ CSV trong prompt; giờ phải lấy qua đây.
    Ô <=0 = CHƯA CẬP NHẬT -> bỏ hẳn, không được in ra thành '0tr' (khách đọc thành miễn phí)."""
    out = []
    cols = [i for i in stone_cols if header[i].strip() == only_stone] if only_stone else stone_cols
    for i in cols:
        v = parse_money(r[i]) if i < len(r) else -1.0
        if v > 0:
            out.append(f"{header[i].strip()} {fmt_money(v)}")
    return "; ".join(out)


def render(results) -> str:
    if not results:
        return "Không có sản phẩm nào trong tầm giá này."
    header, stone_cols, _ = load_rows()
    i_ten, i_dm, i_bc, i_gc = (_col(header, n) for n in
                               ("Tên sản phẩm", "Danh mục", "Bán chạy", "Ghi chú"))
    lines = [f"Tìm thấy {len(results)} sản phẩm (ưu tiên bán chạy + có ảnh, rồi giá tăng dần):"]
    for item in results:
        price, stone_name, r = item[:3]
        only_stone = stone_name if len(item) > 3 and item[3] else None
        head = f"{r[0]} | {_cell(r, i_ten)} | {_cell(r, i_dm)}"
        if _cell(r, i_bc):                   # cột 'Bán chạy' - persona ưu tiên giới thiệu trước
            head += " | BÁN CHẠY"
        if _spec(header, r):
            head += f" | {_spec(header, r)}"
        if _cell(r, i_gc):
            head += f" | ghi chú: {_cell(r, i_gc)}"
        lines.append(head)
        # Giá 0 trong bảng = CHƯA CẬP NHẬT, không phải miễn phí. Phải ghi ra chữ: search() lọc
        # theo tầm giá nên không bao giờ trả mã giá 0, nhưng rows_by_ids() (khách hỏi đích danh
        # mã) thì trả -> bot đọc "0tr" thành "0 đồng" cho khách là mất mặt + mất đơn.
        gia = _prices(header, stone_cols, r, only_stone)
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
    # Thứ tự: bán chạy trước, có ảnh trước, rồi mới tới giá.
    _hdr0, _, _ = load_rows()
    _khoa = [_uu_tien(_hdr0, r) + (p,) for p, _, r in res]
    assert _khoa == sorted(_khoa), "sai thứ tự ưu tiên (bán chạy > có ảnh > giá)"
    _bc = [i for i, (p, _, r) in enumerate(res) if _cell(r, _col(_hdr0, "Bán chạy"))]
    _thuong = [i for i, (p, _, r) in enumerate(res) if not _cell(r, _col(_hdr0, "Bán chạy"))]
    if _bc and _thuong:
        assert max(_bc) < min(_thuong), "mẫu bán chạy phải đứng trước mẫu thường"
    # Giá vẫn phải tăng dần TRONG cùng một nhóm ưu tiên.
    _gia_bc = [p for p, _, r in res if _cell(r, _col(_hdr0, "Bán chạy"))]
    assert _gia_bc == sorted(_gia_bc), "trong nhóm bán chạy phải còn sort theo giá"
    # Mã chưa có giá: hỏi đích danh vẫn trả về, nhưng KHÔNG được hiện thành "0tr"
    _hdr, _cols, _data = load_rows()
    _zero = [r for r in _data if r and r[0].strip() and parse_money(r[_cols[0]]) <= 0]
    if _zero:
        out = render(rows_by_ids([_zero[0][0].strip()]))
        assert "0tr" not in out, f"giá 0 lọt ra dạng số: {out}"
        # Mã trống SẠCH mọi cột đá mới được nói 'chưa có giá'; trống 1 cột thì vẫn phải báo giá
        # các loại đá còn lại, không được im.
        if not any(parse_money(_zero[0][i]) > 0 for i in _cols if i < len(_zero[0])):
            assert "CHƯA CÓ GIÁ" in out, f"mã không có giá nào mà không báo: {out}"

    # Bỏ CSV khỏi prompt -> tool là đường DUY NHẤT lấy thông số. Thiếu = bot bịa với khách.
    _i_dai, _i_rong, _i_cao = (_col(_hdr, n) for n in ("Chieu_Dai", "Chieu_Rong", "Chieu_Cao"))
    _i_tan, _i_kt = _col(_hdr, "Trọng lượng"), _col(_hdr, "Kích thước")
    assert _i_kt >= 0 and _i_kt != _i_dai, "CSV thiếu cột 'Kích thước' (chữ tự do)"
    _spec_row = next((r for r in _data if r and r[0].strip() and _cell(r, _i_tan)
                      and all(_cell(r, i) for i in (_i_dai, _i_rong, _i_cao))), None)
    if _spec_row:
        out = render(rows_by_ids([_spec_row[0].strip()]))
        assert "KT(DxRxC)" in out, f"render mất kích thước: {out}"
        assert "tấn" in out, f"render mất trọng lượng: {out}"
        assert out.count("giá:") == 1 and "Đá" in out, f"render mất giá theo loại đá: {out}"

    # Thiếu cột dài/rộng/cao (30 mã) -> phải lấy ô 'Kích thước' chữ tự do, KHÔNG được im lặng:
    # tool là đường duy nhất bot biết thông số, trống là bot bịa hoặc trả lời trống không.
    _kt_row = next((r for r in _data if r and r[0].strip() and _cell(r, _i_kt)
                    and not all(_cell(r, i) for i in (_i_dai, _i_rong, _i_cao))), None)
    if _kt_row:
        out = render(rows_by_ids([_kt_row[0].strip()]))
        assert _cell(_kt_row, _i_kt) in out, f"mã thiếu chiều mà render mất kích thước: {out}"
        assert "KT(DxRxC)" not in out, f"ghép DxRxC từ số chiều thiếu -> sai nghĩa: {out}"

    # Cột tra theo TÊN header, không theo số thứ tự (chèn cột vào CSV không được làm lệch).
    assert _col(_hdr, "Danh mục") != _col(_hdr, "Thể Loại") >= 0, "không tìm ra cột Thể Loại"
    assert _col(_hdr, "Cột Không Tồn Tại") == -1
    assert _cell(["a", "b"], -1) == "", "cột thiếu phải trả rỗng, không lấy nhầm cột cuối"

    # Lọc theo Thể Loại: giá trị lấy từ CSV, không viết cứng trong code.
    kinds = kinds_available()
    assert "Long đình" in kinds and "Mộ" in kinds, f"kinds_available thiếu: {kinds}"
    i_tl = _col(_hdr, "Thể Loại")
    for k in kinds:
        got = search(None, kind=k, limit=500)
        assert got, f"kind '{k}' không ra mẫu nào"
        assert all(_cell(r, i_tl) == k for _, _, r in got), f"kind '{k}' lọt mẫu loại khác"
    assert search(None, kind="long dinh") == search(None, kind="Long đình"), "kind phải bỏ dấu được"

    # Khách đòi "mẫu khác": loại mã đã gửi thì phải ra mã MỚI, không lặp lại 3 mẫu cũ.
    _l1 = [r[0].strip() for _, _, r in search(None, kind="Long đình", limit=3)]
    _l2 = [r[0].strip() for _, _, r in search(None, kind="Long đình", limit=3, exclude_ids=_l1)]
    assert _l1 and _l2 and not set(_l1) & set(_l2), f"exclude_ids không loại được mã cũ: {_l1} {_l2}"

    # Tìm theo tên: "mộ tròn" phải ra mộ tròn, KHÔNG ra mộ 2 cấp (bug cũ của rows_by_kind).
    tron = search(None, q="mộ tròn", limit=50)
    assert tron and all("tròn" in _cell(r, _col(_hdr, "Tên sản phẩm")).lower() for _, _, r in tron)
    assert search(None, q="mo tron") == tron, "q phải bỏ dấu được"
    assert not search(None, q="mộ tròn bay lơ lửng"), "mọi từ phải khớp, không khớp lỏng"
    # Thể loại nằm ngoài tên sản phẩm (TP01 tên 'Trấn phong', Thể Loại 'Cuốn thư') vẫn tìm ra.
    assert search(None, q="cuốn thư"), "q phải dò cả cột Thể Loại"
    # Lọc chồng: thể loại + trần giá
    # alias granite/G20 maps to GRN, including product-id lookups from image matches.
    grn = search(None, stone="granite", limit=1)
    assert grn == search(None, stone="g20", limit=1), "granite/G20 ph?i c?ng map v? GRN"
    assert grn and "GRN" in render(grn), "loc granite phai in gia GRN"
    m01_grn = render(rows_by_ids(["M01"], stone="granite"))
    assert "GRN" in m01_grn and "xanh" not in m01_grn.lower(), "tra ma + granite chi in gia GRN"

    ld100 = search(1e8, kind="Long đình")
    assert ld100 and all(p <= 1e8 and _cell(r, i_tl) == "Long đình" for p, _, r in ld100)

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
    ap.add_argument("--kind", help=f"Thể loại, vd {kinds_available()}")
    ap.add_argument("--q", help="Từ khoá tên sản phẩm, vd 'mộ tròn'")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        _selftest()
        return
    if not (a.max or a.kind or a.q):
        ap.error("cần ít nhất --max, --kind hoặc --q")
    mx = parse_money(a.max) if a.max else None
    if a.max and mx <= 0:
        ap.error(f"không đọc được --max '{a.max}'")
    mn = parse_money(a.min)
    print(render(search(mx, max(mn, 0.0), a.stone, a.category, a.limit, a.kind, a.q)))


if __name__ == "__main__":
    sys.exit(main())
