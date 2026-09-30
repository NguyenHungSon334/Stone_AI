"""Đối chiếu THÔNG TIN SẢN PHẨM trong tin bot sắp gửi với bảng hàng - chốt chặn cuối chống bịa.

Sự cố thật 28/09/2026, khách Trần Long (psid 38761704246810866): khách hỏi báo giá cả khu,
bot đưa 2 long đình (đúng bảng) rồi tự điền "HR01 Dài 1000 x Cao 810mm khoảng 1.8 triệu mỗi
mét dài" - bảng ghi 4tr/md xanh đen, không có chiều 'Dài 1000'. Khách hiểu tường rào rẻ
bằng nửa giá thật. Luật chữ trong prompt ("TUYỆT ĐỐI không tự suy") đã có, model vẫn trượt,
nên phải kiểm bằng code.

Mọi điều kiểm gắn với MÃ nằm cùng câu (câu không có mã -> mã câu gần nhất phía trước, không
có nữa -> mọi mã trong tin):
  SỐ
  - tiền: khớp giá của mã, ĐÚNG loại đá nêu trong cùng vế câu (không nêu đá -> loại nào cũng
    được); hoặc giá x số lượng; hoặc con số chính khách đã nói (ngân sách).
  - kích thước: mọi chuỗi số có đơn vị mm/cm ("1470 x 970 x 3400mm", "81x127cm"), dạng
    "1m86", và số 3-5 chữ số đứng rời, phải có trong ô kích thước / cột Dài-Rộng-Cao-Hộp thờ.
  - trọng lượng "x tấn": khớp cột Trọng lượng.
  CHỮ (chỉ khi câu nói về đúng 1 mã, để không gán nhầm)
  - thể loại đứng ngay trước mã ("cổng M04" là sai), số mái, số cột/trụ, đơn vị (ngôi / mét
    dài), danh mục An Tâm/Trường Tồn, nhãn "bán chạy".
  - mã viết HOA dạng XX00 không có trong bảng (vd 'LD13' trong khi bảng là 'LĐ13').
Số/đặc điểm khách tự nói ra thì không bắt (khách đòi "long đình 3 mái" -> bot nhắc lại được).

ponytail: không kiểm tổng cộng nhiều mã (a+b) - tổng bị coi là sai và bắt viết lại (persona
cấm đọc tổng khi chưa đủ số lượng). Mô tả tự do (hoa văn, "bề thế") không có cột nào để đối
chiếu nên vẫn trông vào prompt. Lời hứa gửi ảnh do brain.hua_anh_khong_co chặn riêng.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot_tools.find_by_price import _cell, _col, fold, load_rows, parse_money

_SAI_SO_TIEN = 0.03          # "khoảng 79.7 triệu" cho 79.701.333, "khoảng 80 triệu" cũng qua
_SAI_SO_TIEN_TUYET_DOI = 60_000
_SAI_SO_TAN = 0.05
_SO_LUONG_MAX = 1000         # số lượng hợp lý để nhân đơn giá (ngôi, mét dài, bộ)
_KHONG_PHAI_MA = {"G20"}     # tên đá, không phải mã sản phẩm
_KT_ROI_MIN = 300            # số RỜI (không kèm mm/cm) dưới mức này là số đếm ("hơn 100 mẫu")

# Tên loại đá khách/bot hay gọi (đã bỏ dấu) -> cột giá trong bảng. Dò cụm dài trước.
_DA = (("xanh binh dinh", "Đá xanh Bình Định"), ("xam", "Đá xám BĐ"),
       ("xanh den", "Đá xanh đen"), ("xanh reu", "Đá xanh rêu"),
       ("grn", "Đá GRN đen Ấn Độ"), ("granite", "Đá GRN đen Ấn Độ"),
       ("granit", "Đá GRN đen Ấn Độ"), ("an do", "Đá GRN đen Ấn Độ"),
       # KHÔNG dò "trắng"/"Yên Bái" rời: "trang trọng" bỏ dấu thành "trang", Yên Bái có thể
       # là địa chỉ khách -> gán nhầm loại đá cho con số giá cùng vế.
       ("trang yen bai", "Đá trắng Yên Bái"), ("da trang", "Đá trắng Yên Bái"))
# Từ chỉ thể loại đứng ngay trước mã -> các Thể Loại chấp nhận (đã bỏ dấu).
_LOAI = {"mo": {"mo"}, "long dinh": {"long dinh", "lau tho"}, "lang tho": {"long dinh", "lau tho"},
         "lau tho": {"lau tho", "long dinh"}, "cong": {"cong"}, "hang rao": {"hang rao"},
         "lan can": {"hang rao"}, "tuong rao": {"hang rao"},
         "cuon thu": {"tran phong", "binh phong"}, "tran phong": {"tran phong", "binh phong"},
         "binh phong": {"tran phong", "binh phong"}, "tam son": {"tam son", "ban le"},
         "ban le": {"tam son", "ban le"}, "lu huong": {"lu huong"}, "bat huong": {"bat huong"}}
_SO_CHU = {"mot": "1", "hai": "2", "ba": "3", "bon": "4", "tu": "4"}
_LE_CHU = {"mot": 1, "hai": 2, "ba": 3, "tu": 4, "bon": 4, "ruoi": 5, "nam": 5,
           "sau": 6, "bay": 7, "tam": 8, "chin": 9}   # phần lẻ sau "m", đã bỏ dấu
# Chỉ 2 đơn vị này hay bị nhầm và nhầm là sai tiền cả khu (giá mộ /ngôi đọc thành /mét...).
_DON_VI_CHAT = {"ngoi": "ngoi", "met dai": "met dai", "md": "met dai", "m dai": "met dai"}

_MA_RE = re.compile(r"(?<!\w)([^\W\d_]{1,4}\d{2})(?:\.\d+)?(?!\w)")
_DON_VI = r"(tỷ|tỉ|ty|triệu|trieu|tr|củ)"
_SO = r"\d+(?:[.,]\d+)?"
# "67 tới 212 triệu": số đầu không có đơn vị, mượn đơn vị số sau.
_TIEN_RE = re.compile(rf"({_SO})(?:\s*(?:tới|đến|toi|den|-|–)\s*({_SO}))?\s*{_DON_VI}(?!\w)", re.I)
_TIEN_DU_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3}){2,})(?![\d.,])\s*(?:đồng|đ|vnđ|vnd)?", re.I)
_TAN_RE = re.compile(rf"({_SO})\s*tấn", re.I)
# Chuỗi kích thước có đơn vị: "1470 x Rộng 970 x Cao 3400mm", "D5200xR860mm", "81x127cm".
_CHUOI_KT_RE = re.compile(rf"({_SO}(?:\s*[x×*]\s*[^\W\d_]{{0,4}}\s*{_SO})*)\s*(mm|cm)(?!\w)", re.I)
_MET_RE = re.compile(r"(?<![\d.,])(\d)\s*m\s*(\d{1,2})(?!\w|[.,]\d)")      # 1m86, 2m6, "2 m 6"
_KT_ROI_RE = re.compile(r"(?<![\d.,])(\d{3,5})(?![\d.,])")
_SO_BAT_KY_RE = re.compile(_SO)
_CAU_RE = re.compile(r"((?<=[.!?])\s+|\n+)")   # nhóm bắt -> split giữ lại dấu ngắt câu/xuống dòng
# Vế câu: phạm vi gắn loại đá với con số giá. Phẩy PHẢI kèm khoảng trắng: "6,5 triệu" là số thập phân.
_VE_RE = re.compile(r"[;:()]|,(?=\s)")


def _so(s: str) -> float:
    return float(s.replace(",", "."))


def _met(m: re.Match) -> tuple[int, int]:
    """'1m86' -> (1860, 10), '2m6' -> (2600, 50): giá trị mm + dung sai theo độ chi tiết."""
    le = m.group(2)
    return int(m.group(1)) * 1000 + int(le) * (100 if len(le) == 1 else 10), (50 if len(le) == 1 else 10)


def _bang() -> dict:
    """{MÃ: thông tin} dựng từ CSV mỗi lần gọi (file nhỏ, sync bảng xong là có hiệu lực ngay)."""
    header, stone_cols, data = load_rows()
    i_kt, i_tan = _col(header, "Kích thước"), _col(header, "Trọng lượng")
    i_so = [_col(header, n) for n in ("Chieu_Dai", "Chieu_Rong", "Chieu_Cao", "Kich_Thuoc_Hop_Tho")]
    i_ten, i_tl, i_dv = _col(header, "Tên sản phẩm"), _col(header, "Thể Loại"), _col(header, "Đơn vị")
    i_dm, i_bc = _col(header, "Danh mục"), _col(header, "Bán chạy")
    out = {}
    for r in data:
        kt = {int(x) for x in re.findall(r"\d{2,5}", _cell(r, i_kt))}
        for i in i_so:
            try:
                kt.add(int(float(_cell(r, i))))
            except ValueError:
                pass
        out[r[0].strip().upper()] = {
            "gia": {header[i].strip(): v for i in stone_cols if (v := parse_money(_cell(r, i))) > 0},
            "kt": kt,
            "tan": [v for v in [parse_money(_cell(r, i_tan))] if v > 0],
            "ten": fold(_cell(r, i_ten)), "loai": fold(_cell(r, i_tl)), "don_vi": fold(_cell(r, i_dv)),
            "danh_muc": _cell(r, i_dm), "ban_chay": bool(_cell(r, i_bc)),
        }
    return out


def _tien_trong(text: str) -> list[tuple[str, float]]:
    """[(chuỗi gốc, số tiền VND)] mọi con số tiền trong text."""
    out = []
    for m in _TIEN_RE.finditer(text):
        mul = 1e9 if fold(m.group(3)).startswith(("ty", "ti")) else 1e6
        out += [(m.group(0), _so(g) * mul) for g in m.groups()[:2] if g]
    out += [(m.group(0), float(re.sub(r"[.,]", "", m.group(1)))) for m in _TIEN_DU_RE.finditer(text)]
    return out


def _da_trong(text: str) -> set[str]:
    """Các cột giá loại đá được nhắc trong text."""
    t, out = fold(text), set()
    for ten, cot in _DA:
        if re.search(rf"\b{ten}\b", t):
            out.add(cot)
            t = t.replace(ten, " ")
    return out


def _khop(x: float, dung, sai_so: float, tuyet_doi: float = 0) -> bool:
    return any(abs(x - v) <= max(v * sai_so, tuyet_doi) for v in dung)


class _Khach:
    """Mọi thứ khách TỰ nói ra - bot nhắc lại thì không tính là bịa."""

    def __init__(self, loi_khach: list[str]):
        text = " ".join(loi_khach)
        self.fold = fold(text)
        self.tien = [v for _, v in _tien_trong(text)]
        so = {int(x) for x in re.findall(r"\d+", text)}
        self.so = so | {x * 10 for x in so}                          # khách nói cm -> bot đọc mm
        self.so |= {round(_so(x) * 1000) for x in re.findall(r"\d+[.,]\d+", text)}   # 1,07m
        self.so |= {_met(m)[0] for m in _MET_RE.finditer(text)}                       # 2 m 6
        # "1 m tư", "2 m rưỡi" - khách nói số lẻ bằng chữ, bot viết lại thành 1m4 / 2m5.
        self.so |= {int(a) * 1000 + _LE_CHU[b] * 100 for a, b in re.findall(
            rf"(?<!\d)(\d)\s*m\s*({'|'.join(_LE_CHU)})\b", self.fold)}

    def co_noi(self, cum: str) -> bool:
        return bool(re.search(rf"\b{cum}\b", self.fold))


def _kiem_gia(cau: str, ma: list[str], bang: dict, khach: _Khach, so_luong: list[float]) -> list[str]:
    loi = []
    ten_ma = ", ".join(ma) or "mã nào"
    da_ca_cau = _da_trong(cau)
    for ve in _VE_RE.split(cau):
        # Loại đá nêu trong CÙNG VẾ với con số; vế không nêu mà cả câu chỉ nêu 1 loại -> loại đó.
        da = _da_trong(ve) or (da_ca_cau if len(da_ca_cau) == 1 else set())
        gia = [v for c in ma for cot, v in bang[c]["gia"].items() if not da or cot in da]
        gia_nhan = gia + [g * n for g in gia for n in so_luong]
        for goc, v in _tien_trong(ve):
            if _khop(v, khach.tien, 0.001) or _khop(v, gia_nhan, _SAI_SO_TIEN, _SAI_SO_TIEN_TUYET_DOI):
                continue
            loai = f" loại {', '.join(sorted(da))}" if da else ""
            loi.append(f"giá '{goc}' không khớp bảng hàng của {ten_ma}{loai}")
    return loi


def _kiem_kich_thuoc(cau: str, ma: list[str], ma_cau: list[str], bang: dict, khach: _Khach) -> list[str]:
    loi, ten_ma = [], ", ".join(ma) or "mã nào"
    kt = {k for c in ma for k in bang[c]["kt"]}

    def sai(mm: int, tol: int = 0) -> bool:
        return not any(abs(mm - k) <= tol for k in kt | khach.so)

    con_lai = cau
    for goc, _ in _tien_trong(cau):
        con_lai = con_lai.replace(goc, " ")
    for m in _CHUOI_KT_RE.finditer(con_lai):
        cm = m.group(2).lower() == "cm"
        # Cỡ cm mà câu KHÔNG có mã = kiến thức chung thước Lỗ Ban ("phổ biến 81x127cm").
        if cm and not ma_cau:
            continue
        for x in _SO_BAT_KY_RE.findall(m.group(1)):
            mm = round(_so(x) * (10 if cm else 1))
            if sai(mm):
                loi.append(f"kích thước '{x}{m.group(2)}' không có trong bảng hàng của {ten_ma}")
    con_lai = _CHUOI_KT_RE.sub(" ", con_lai)
    if ma_cau:                          # "cao 1m86" chỉ xét khi câu nói về mã cụ thể
        for m in _MET_RE.finditer(con_lai):
            mm, tol = _met(m)
            if sai(mm, tol):
                loi.append(f"kích thước '{m.group(0)}' không có trong bảng hàng của {ten_ma}")
    for goc in _TAN_RE.findall(con_lai):
        con_lai = con_lai.replace(goc, " ")
    for goc in _KT_ROI_RE.findall(con_lai):
        n = int(goc)
        if n < _KT_ROI_MIN or not sai(n) or re.search(rf"(?:năm\s*|/){goc}", con_lai):
            continue
        loi.append(f"kích thước '{goc}' không có trong bảng hàng của {ten_ma}")
    return loi


def _kiem_tan(cau: str, ma: list[str], bang: dict) -> list[str]:
    tan = [t for c in ma for t in bang[c]["tan"]]
    return [f"trọng lượng '{g} tấn' không khớp bảng hàng của {', '.join(ma) or 'mã nào'}"
            for g in _TAN_RE.findall(cau) if not _khop(_so(g), tan, _SAI_SO_TAN)]


def _kiem_chu(cau: str, ma: str, sp: dict, khach: _Khach) -> list[str]:
    """Đặc điểm CHỮ của đúng 1 mã trong câu: thể loại, số mái/cột, đơn vị, danh mục, bán chạy."""
    loi, t = [], fold(cau)
    loai_re = "|".join(sorted(_LOAI, key=len, reverse=True))
    for m in re.finditer(rf"\b({loai_re})\b(?:\s+(?:da|mau|tho|chung|don|doi|vuong|tron|to))*\s+{fold(ma)}\b", t):
        if not any(sp["loai"].startswith(x) or x in sp["ten"] for x in _LOAI[m.group(1)]):
            loi.append(f"{ma} là '{sp['loai']}', không phải '{m.group(1)}'")
    for m in re.finditer(r"\b(\d|mot|hai|ba|bon|tu)\s+(mai|cot|tru)\b", t):
        n, loai = _SO_CHU.get(m.group(1), m.group(1)), m.group(2)
        loai = "cot" if loai == "tru" else loai
        if f"{n} {loai}" not in sp["ten"] and not khach.co_noi(m.group(0)) and not khach.co_noi(f"{n} {loai}"):
            loi.append(f"{ma} không phải loại '{m.group(0)}' (tên trong bảng: {sp['ten']})")
    for m in re.finditer(r"(?:moi|/)\s*(ngoi|met dai|m dai|md)\b", t):
        if _DON_VI_CHAT[m.group(1)] != sp["don_vi"]:
            loi.append(f"{ma} tính theo '{sp['don_vi']}', không phải '{m.group(1)}'")
    if sp["don_vi"] in _DON_VI_CHAT.values() and (m := re.search(r"(?:moi|/)\s*(bo|cai)\b", t)):
        loi.append(f"{ma} tính theo '{sp['don_vi']}', không phải '{m.group(1)}'")
    for dm in ("An Tâm", "Trường Tồn"):
        if dm in cau and sp["danh_muc"] != dm:
            loi.append(f"{ma} thuộc danh mục '{sp['danh_muc'] or 'chưa gắn'}', không phải '{dm}'")
    if "ban chay" in t and not sp["ban_chay"]:
        loi.append(f"{ma} KHÔNG gắn nhãn bán chạy trong bảng hàng")
    return loi


def _loi_theo_cau(reply: str, loi_khach: list[str]) -> list[tuple[int, str]]:
    """[(chỉ số câu, lỗi)] - gắn lỗi vào câu để cat_cau_sai bỏ đúng câu đó."""
    bang, khach = _bang(), _Khach(loi_khach)
    so_luong = [n for x in _SO_BAT_KY_RE.findall(reply + " " + " ".join(loi_khach))
                if 1 <= (n := _so(x)) <= _SO_LUONG_MAX]
    loi, ma_truoc = [], []
    ma_ca_tin = list(dict.fromkeys(m.upper() for m in _MA_RE.findall(reply) if m.upper() in bang))
    for i, cau in enumerate(_CAU_RE.split(reply)[::2]):
        ma_cau = list(dict.fromkeys(m.upper() for m in _MA_RE.findall(cau) if m.upper() in bang))
        ma = ma_cau or ma_truoc or ma_ca_tin
        ma_truoc = ma
        for m in _MA_RE.findall(cau):   # mã lạ: chỉ bắt dạng viết HOA, không nhầm chữ thường
            if m.isupper() and m.upper() not in bang and m.upper() not in _KHONG_PHAI_MA:
                goi_y = [c for c in bang if fold(c) == fold(m)]
                loi.append((i, f"mã '{m}' không có trong bảng hàng"
                            + (f" (đúng là '{goi_y[0]}')" if goi_y else "")))
        cac_loi = (_kiem_gia(cau, ma, bang, khach, so_luong)
                   + _kiem_kich_thuoc(cau, ma, ma_cau, bang, khach) + _kiem_tan(cau, ma, bang))
        if len(ma_cau) == 1:
            cac_loi += _kiem_chu(cau, ma_cau[0], bang[ma_cau[0]], khach)
        loi += [(i, x) for x in cac_loi]
    return loi


def kiem(reply: str, loi_khach: list[str]) -> list[str]:
    """Các lỗi thông tin sản phẩm trong reply (rỗng = sạch). Mỗi lỗi là 1 câu đọc được cho cả
    model (khi bắt viết lại) lẫn admin (cảnh báo)."""
    return list(dict.fromkeys(m for _, m in _loi_theo_cau(reply, loi_khach)))


def cat_cau_sai(reply: str, loi_khach: list[str]) -> str:
    """Lưới cuối khi model viết lại vẫn sai: bỏ hẳn câu chứa thông tin sai. Còn hơn gửi số bịa."""
    sai = {i for i, _ in _loi_theo_cau(reply, loi_khach)}
    manh = _CAU_RE.split(reply)            # [câu, ngắt, câu, ngắt, ...]
    return "".join(manh[k] + (manh[k + 1] if k + 1 < len(manh) else "")
                   for k in range(0, len(manh), 2) if k // 2 not in sai).strip()
