"""
Kéo bảng hàng từ Lark Base (bảng "3.4 Sản phẩm") -> ghi đè Danh_Muc_San_Pham.csv.

Chạy lại mỗi khi nhân viên sửa bảng trên Base:
  python bot_tools/sync_bang_hang.py            # ghi CSV
  python bot_tools/sync_bang_hang.py --dry-run  # chỉ in thống kê

Giữ NGUYÊN header CSV cũ - find_by_price đọc theo tên cột nên mọi lớp sau không phải đổi.
Bảng mới KHÔNG có cột tên sản phẩm -> dựng tên từ thể loại + đặc điểm phân biệt có sẵn
(dáng mộ, số mái, số cột cổng...). Tên là đường khách tìm bằng lời ("mộ tròn"), để trơ
thể loại thì 33 mã cùng tên "Mộ" và tool tìm-theo-tên chết.

Mã chưa gắn giá VẪN vào bảng: đó là hàng thiết kế riêng (bát hương, lư hương, đèn đá...),
có ảnh đủ để chào khách. Bot giới thiệu mẫu rồi mời khách để lại SĐT cho chuyên gia báo giá -
giữ được khách, hơn hẳn việc giấu luôn mẫu.
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from bot_tools.find_by_price import CSV, parse_money
from bot_tools.lark_image import _la_anh, _tenant_token, request_retry

HEADER = ["Mã Sản Phẩm", "Tên sản phẩm", "Bán chạy", "Danh mục", "Thể Loại", "Đơn vị",
          "Kích thước", "Chieu_Dai_Max (mm)", "Chieu_Rong_Max (mm)", "Chieu_Cao_Max (mm)",
          "Kich_Thuoc_Hop_Tho (mm)", "Khối lượng (m3)", "Trọng lượng (tấn)",
          "Đá xanh đen", "Đá xanh rêu", "Đá xám BĐ", "Đá GRN đen Ấn Độ", "Ghi chú",
          "Đá xanh Bình Định", "Đá trắng Yên Bái", "Có ảnh"]

# cột CSV giá <- trường "Giá gợi ý ..." của Base (đã làm tròn sẵn bên Base)
GIA = {"Đá xanh đen": "Giá gợi ý xanh đen",
       "Đá xanh rêu": "Giá gợi ý xanh rêu",
       "Đá xám BĐ": "Giá gợi ý xám BĐ",
       "Đá GRN đen Ấn Độ": "Giá gợi ý Granite đen AĐ",
       "Đá xanh Bình Định": "Giá gợi ý xanh BĐ",
       "Đá trắng Yên Bái": "Giá gợi ý trắng Yên Bái"}

# Trường dựng TÊN sản phẩm (bảng không có cột tên sẵn).
TA = ["Dáng mộ", "Quy mô sử dụng", "Số mái", "Số cột cổng", "Hình dạng(cuốn thư)"]

LAY = ["Mã sản phẩm", "Nhóm hạng mục (helper)", "Nhãn bán chạy", "Tầng tiêu chuẩn",
       "Đơn vị tính", "Dài (mm)", "Rộng (mm)", "Cao (mm)", "Kích thước hộp thờ (mm)",
       "Khối lượng (m3)", *TA, *GIA.values(), *config.LARK_IMAGE_FIELDS]

TAN_MOI_M3 = 3.0      # quy đổi khối lượng -> trọng lượng, giữ đúng hệ số của bảng cũ


def phang(v) -> str:
    """Ô Bitable -> chuỗi. Formula/text trả dict{value:[{text}]} hoặc list, select trả list."""
    if v is None:
        return ""
    if isinstance(v, dict):
        return phang(v.get("value") if "value" in v else v.get("text"))
    if isinstance(v, list):
        return ", ".join(x for x in (phang(i) for i in v) if x)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def keo_records() -> list[dict]:
    """Toàn bộ record của bảng sản phẩm (phân trang hết)."""
    tok = _tenant_token()
    url = (f"{config.LARK_DOMAIN}/open-apis/bitable/v1/apps/{config.LARK_BASE_APP_TOKEN}"
           f"/tables/{config.LARK_TABLE_ID}/records/search?page_size=500")
    out, page = [], ""
    while True:
        r = request_retry("POST", url + (f"&page_token={page}" if page else ""),
                          headers={"Authorization": f"Bearer {tok}"},
                          json={"field_names": LAY, "automatic_fields": False}, timeout=30.0)
        d = r.json()
        if d.get("code") != 0:
            raise RuntimeError(f"Lark search lỗi: {d.get('code')} {d.get('msg')}")
        data = d.get("data") or {}
        out += [it.get("fields") or {} for it in (data.get("items") or [])]
        page = data.get("page_token") or ""
        if not data.get("has_more") or not page:
            return out


def _kich_thuoc(f: dict) -> str:
    """'KT: D1070xR690xC760mm' - bỏ chiều nào trống. Rỗng nếu không có chiều nào."""
    phan = [(k, phang(f.get(c))) for k, c in (("D", "Dài (mm)"), ("R", "Rộng (mm)"), ("C", "Cao (mm)"))]
    co = [f"{k}{v}" for k, v in phan if v]
    return "KT: " + "x".join(co) + "mm" if co else ""


def _ten(f: dict, the_loai: str) -> str:
    """'Mộ' -> 'Mộ tròn tổ', 'Long đình' -> 'Long đình 2 mái'. Không có đặc điểm thì trả
    trơ thể loại."""
    dang = phang(f.get("Dáng mộ")).split(" - ")[0].lower()        # 'Tròn - đa giác' -> 'tròn'
    # 'Mộ tổ' -> 'tổ': thể loại đã là 'Mộ', không lặp thành 'Mộ mộ tổ'.
    quy_mo = phang(f.get("Quy mô sử dụng")).lower().removeprefix("mộ ")
    mai, cot = phang(f.get("Số mái")), phang(f.get("Số cột cổng"))
    hinh = phang(f.get("Hình dạng(cuốn thư)")).lower()
    them = [x for x in (dang, quy_mo if quy_mo != "đơn" else "",
                        f"{mai} mái" if mai else "", f"{cot} cột" if cot else "",
                        hinh if hinh and hinh != the_loai.lower() else "") if x]
    return " ".join([the_loai, *them])


def thanh_dong(f: dict) -> list[str] | None:
    """1 record Base -> 1 dòng CSV. None nếu record chưa có mã (dòng nháp trên Base)."""
    ma = phang(f.get("Mã sản phẩm"))
    if not ma:
        return None
    the_loai = phang(f.get("Nhóm hạng mục (helper)"))
    m3 = phang(f.get("Khối lượng (m3)"))
    co_anh = any(_la_anh(a) for c in config.LARK_IMAGE_FIELDS for a in (f.get(c) or []))
    row = {
        "Mã Sản Phẩm": ma,
        "Tên sản phẩm": _ten(f, the_loai),
        "Bán chạy": "Bán chạy" if phang(f.get("Nhãn bán chạy")) == "Bán chạy" else "",
        "Danh mục": phang(f.get("Tầng tiêu chuẩn")),
        "Thể Loại": the_loai,
        "Đơn vị": phang(f.get("Đơn vị tính")),
        "Kích thước": _kich_thuoc(f),
        "Chieu_Dai_Max (mm)": phang(f.get("Dài (mm)")),
        "Chieu_Rong_Max (mm)": phang(f.get("Rộng (mm)")),
        "Chieu_Cao_Max (mm)": phang(f.get("Cao (mm)")),
        "Kich_Thuoc_Hop_Tho (mm)": phang(f.get("Kích thước hộp thờ (mm)")),
        "Khối lượng (m3)": m3,
        "Trọng lượng (tấn)": f"{float(m3) * TAN_MOI_M3:.3f}".rstrip("0").rstrip(".") if m3 else "",
        "Ghi chú": "",
        "Có ảnh": "TRUE" if co_anh else "FALSE",
        **{cot: phang(f.get(src)) for cot, src in GIA.items()},
    }
    return [row[c] for c in HEADER]


def dong_bo(dry_run: bool = False) -> tuple[int, int]:
    rows = [r for r in (thanh_dong(f) for f in keo_records()) if r]
    i_gia = [HEADER.index(c) for c in GIA]
    # Giá 0 = CHƯA có giá, không phải hàng miễn phí: Base trả "0" cho ô công thức thiếu đầu vào.
    khong_gia = [r[0] for r in rows if not any(parse_money(r[i]) > 0 for i in i_gia)]
    if khong_gia:
        print(f"[bảng hàng] {len(khong_gia)} mã chưa có giá -> vào bảng dạng THIẾT KẾ RIÊNG: "
              + ", ".join(khong_gia), file=sys.stderr)
    rows.sort(key=lambda r: r[0])
    if not rows:
        raise RuntimeError("Base không trả record nào - KHÔNG ghi đè CSV")
    if not dry_run:
        with open(CSV, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows([HEADER] + rows)
    return len(rows), sum(r[HEADER.index("Có ảnh")] == "TRUE" for r in rows), len(khong_gia)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    n, anh, tkr = dong_bo(a.dry_run)
    print(f"Base: {n} mã | có ảnh: {anh} | thiết kế riêng (chưa giá): {tkr}"
          + (" (dry-run, chưa ghi)" if a.dry_run else f" -> đã ghi {CSV.name}"))
