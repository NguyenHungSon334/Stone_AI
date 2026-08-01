"""
Quét Lark Base (cột Ảnh) -> ghi cột "Có ảnh" (TRUE/FALSE) vào Danh_Muc_San_Pham.csv.

Chạy lại mỗi khi bổ sung ảnh vào Base:
  python bot_tools/sync_anh_flag.py            # ghi CSV
  python bot_tools/sync_anh_flag.py --dry-run  # chỉ xem thống kê, không ghi
  python bot_tools/sync_anh_flag.py --prune    # ghi cờ + XOÁ dòng không có ảnh

Biến thể mã (M01.2) dùng chung ảnh mã gốc -> so bằng lark_image.base_code().
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from bot_tools.find_by_price import CSV
from bot_tools.lark_image import _la_anh, _tenant_token, base_code, request_retry

FLAG_COL = "Có ảnh"


def codes_with_image() -> set[str]:
    """Mã (đã cắt biến thể) có >=1 file trong cột Ảnh của Base."""
    tok = _tenant_token()
    url = (f"{config.LARK_DOMAIN}/open-apis/bitable/v1/apps/{config.LARK_BASE_APP_TOKEN}"
           f"/tables/{config.LARK_TABLE_ID}/records/search?page_size=500")
    out: set[str] = set()
    page = ""
    while True:
        r = request_retry("POST", url + (f"&page_token={page}" if page else ""),
                          headers={"Authorization": f"Bearer {tok}"},
                          json={"field_names": [config.LARK_PRODUCT_FIELD, config.LARK_IMAGE_FIELD],
                                "automatic_fields": False},
                          timeout=30.0)
        d = r.json()
        if d.get("code") != 0:
            raise RuntimeError(f"Lark search lỗi: {d.get('code')} {d.get('msg')}")
        data = d.get("data") or {}
        for item in (data.get("items") or []):
            f = item.get("fields") or {}
            raw = f.get(config.LARK_PRODUCT_FIELD)
            # Field text của Bitable có thể là str hoặc list[{text:...}].
            code = raw if isinstance(raw, str) else "".join(
                seg.get("text", "") for seg in (raw or []) if isinstance(seg, dict))
            # Chỉ tính ẢNH thật: record chỉ có video .MOV đính kèm mà đánh "Có ảnh" là bot
            # giới thiệu mẫu đó rồi khách không nhận được hình nào.
            if code and any(_la_anh(a) for a in (f.get(config.LARK_IMAGE_FIELD) or [])):
                out.add(base_code(code))
        page = data.get("page_token") or ""
        if not data.get("has_more") or not page:
            return out


def write_flag(have: set[str], dry_run: bool = False, prune: bool = False) -> tuple[int, int]:
    """Thêm/cập nhật cột FLAG_COL trong CSV. prune=True thì xoá luôn dòng FALSE.

    Trả (số dòng TRUE, tổng dòng trước khi xoá).
    """
    with open(CSV, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    if FLAG_COL in header:
        col = header.index(FLAG_COL)
    else:
        col = len(header)
        header.append(FLAG_COL)
    ok = 0
    for row in rows[1:]:
        while len(row) <= col:
            row.append("")
        flag = base_code(row[0]) in have if row[0].strip() else False
        row[col] = "TRUE" if flag else "FALSE"
        ok += flag
    total = len(rows) - 1
    out = [header] + [r for r in rows[1:] if r[col] == "TRUE"] if prune else rows
    if not dry_run:
        with open(CSV, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(out)
    return ok, total


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    prune = "--prune" in sys.argv
    have = codes_with_image()
    ok, total = write_flag(have, dry, prune)
    print(f"Base có ảnh: {len(have)} mã gốc | CSV: {ok}/{total} dòng TRUE"
          f"{f' | xoá {total - ok} dòng không ảnh' if prune else ''}"
          f"{' (dry-run, chưa ghi)' if dry else ' -> đã ghi ' + CSV.name}")
