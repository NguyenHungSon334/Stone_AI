"""Tin gửi khách: sạch Markdown, và giới thiệu mẫu là phải kèm ảnh."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import brain

# Tin thật đã gửi khách 31/07 (Messenger không render Markdown nên khách đọc nguyên ký hiệu).
TIN_THAT = """Dạ, em đã nhận được SĐT của Bác là **0702710469** ạ.
Với số điện thoại này, chuyên gia bên em sẽ liên hệ ngay:
*   Mẫu mộ nguyên khối **Dài 2.35m x Rộng 1.17m** (100-120 triệu).
*   Các phương án quy hoạch khuôn viên **6m x 7.5m** tại **Quảng Nam**."""


def test_boc_markdown_khoi_tin_that():
    sach = brain._sach_markdown(TIN_THAT)

    assert "*" not in sach and "`" not in sach and "#" not in sach
    # Nội dung phải còn nguyên, chỉ mất ký hiệu.
    for phai_con in ("0702710469", "Dài 2.35m x Rộng 1.17m", "100-120 triệu", "Quảng Nam"):
        assert phai_con in sach, phai_con
    # Bullet bỏ đi nhưng 2 ý không được dính vào nhau thành một câu.
    assert "triệu).\nCác phương án" in sach


def test_boc_moi_kieu_markdown():
    assert brain._sach_markdown("**đậm** và *nghiêng*") == "đậm và nghiêng"
    assert brain._sach_markdown("## Tiêu đề") == "Tiêu đề"
    assert brain._sach_markdown("`M01`") == "M01"
    assert brain._sach_markdown("1. mẫu A\n2. mẫu B") == "mẫu A\nmẫu B"
    assert brain._sach_markdown("- mẫu A\n• mẫu B") == "mẫu A\nmẫu B"
    assert brain._sach_markdown("dấu ** lẻ không cặp") == "dấu lẻ không cặp"


def test_khong_dung_toi_chu_va_so_binh_thuong():
    for cau in ("Mộ đơn M04 khoảng 3,3 triệu mỗi ngôi ạ.",
                "KT 1270x790x760mm, 0.753 tấn.",
                "Bác cho em xin SĐT 0912345678 nhé!"):
        assert brain._sach_markdown(cau) == cau, cau


def _hist(*cap):
    """[(role, content), ...] -> log."""
    return [{"role": r, "content": c} for r, c in cap]


def test_nhac_lai_ma_o_luot_sau_van_gui_anh(monkeypatch):
    """Ca thật: khách hỏi 'long đình' hôm sau, LD01/LD05 đã nhắc hôm trước -> luật cũ im lặng
    không gửi ảnh, khách kêu 'đâu có thấy mẫu nào'."""
    monkeypatch.setattr(brain.lark_image, "get_image_tokens", lambda code: [f"tok-{code}"])
    hist = _hist(("user", "long đình"), ("assistant", "LD01 và LD05 ạ"),
                 ("user", "mộ đôi"), ("assistant", "MD01 ạ"),
                 ("user", "cái LD01 ấy"), ("assistant", "dạ vâng"),
                 ("user", "long đình"))

    markers = brain._image_markers(hist, "LD01 và LD05 giá khoảng...", "long đình")
    assert "tok-LD01" in markers and "tok-LD05" in markers


def test_hoi_don_ve_mot_mau_van_co_anh_nhung_la_anh_khac(monkeypatch):
    """Nhắc lại cùng mã -> vẫn gửi, và _next_image_token xoay vòng nên là ẢNH GÓC KHÁC."""
    monkeypatch.setattr(brain.lark_image, "get_image_tokens",
                        lambda code: [f"{code}-a1", f"{code}-a2", f"{code}-a3"])
    hist = _hist(("user", "long đình"), ("assistant", "LD01 giá khoảng 77 triệu ạ"),
                 ("user", "nặng bao nhiêu tấn"))

    lan1 = brain._image_markers(hist, "LD01 nặng 5.4 tấn ạ", "nặng bao nhiêu tấn", "p-xoay")
    lan2 = brain._image_markers(hist, "LD01 cao 2.75m ạ", "cao bao nhiêu", "p-xoay")

    assert "LD01-a1" in lan1 and "LD01-a2" in lan2, "phải xoay sang ảnh khác, không lặp ảnh cũ"


def test_khach_doi_anh_thi_gui_ke_ca_vua_gui(monkeypatch):
    """Khách gõ thẳng mã kèm đòi ảnh -> gửi cả mã trong tin của khách."""
    monkeypatch.setattr(brain.lark_image, "get_image_tokens", lambda code: [f"tok-{code}"])
    hist = _hist(("user", "long đình"), ("assistant", "LD01 giá khoảng 77 triệu ạ"),
                 ("user", "cho xem ảnh LD01"))

    assert "tok-LD01" in brain._image_markers(hist, "Dạ ảnh LD01 đây ạ", "cho xem ảnh LD01")


def test_ma_khach_go_lan_dau_van_duoc_gui_anh(monkeypatch):
    """Bot nhắc lại mã khách hỏi -> vẫn kèm ảnh ngay lượt đầu."""
    monkeypatch.setattr(brain.lark_image, "get_image_tokens", lambda code: [f"tok-{code}"])
    hist = _hist(("user", "cho hỏi mẫu LD07"))

    assert "tok-LD07" in brain._image_markers(hist, "Dạ LD07 giá khoảng... ạ", "cho hỏi mẫu LD07")
