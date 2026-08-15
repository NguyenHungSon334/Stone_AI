"""Tiện ích dùng chung: chuẩn hóa psid, đọc/ghi JSON an toàn. Chỉ phụ thuộc stdlib (không vòng import)."""
import json
import re
from pathlib import Path


def safe_psid(psid) -> str:
    """psid -> tên file/key an toàn (bỏ ký tự lạ, tối đa 80). Rỗng -> 'unknown'."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(psid))[:80] or "unknown"


_PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)\d{9,10}(?!\d)")
_PHONE_SEP_RE = re.compile(r"[\s.\-()]")


def tim_sdt(text: str) -> str:
    """SĐT VN đầu tiên trong tin, CHUẨN HOÁ về 0xxxxxxxxx. "" nếu không có.

    Một nguồn duy nhất cho cả brain (ghi hồ sơ khách) lẫn messenger (báo admin, ghi CRM).
    Trước đây mỗi bên một bản: messenger trả nguyên "+84912345678" còn hồ sơ/CRM lưu
    "0912345678" -> tra CRM theo số không khớp, khách bị coi như chưa có lead.
    """
    m = _PHONE_RE.search(_PHONE_SEP_RE.sub("", text or ""))
    if not m:
        return ""
    so = m.group(0).lstrip("+")
    return "0" + so[2:] if so.startswith("84") else so


def write_json_atomic(path: Path, obj) -> None:
    """Ghi JSON kiểu atomic (tmp + rename) -> chết giữa chừng không hỏng file gốc.
    KHÔNG bọc try: caller tự quyết log/fallback."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default=None):
    """Đọc JSON, lỗi/thiếu file -> trả default (fallback an toàn)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
