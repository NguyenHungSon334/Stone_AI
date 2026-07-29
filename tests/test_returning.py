"""Test phần thuần logic của returning.py (không chạm mạng, không chạm đĩa)."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import returning


def _fb_time(s: str) -> str:
    """Giả lập messenger._fb_time_to_local: ISO FB -> '%Y-%m-%d %H:%M:%S'."""
    return s.replace("T", " ")[:19]


def test_doi_tin_fb_dao_thu_tu_va_gan_dung_vai():
    # Arrange - Graph trả MỚI TRƯỚC
    tin = [
        {"from": {"id": "PAGE"}, "message": "Dạ em chào Bác",
         "created_time": "2026-07-20T10:05:00+0000"},
        {"from": {"id": "KHACH"}, "message": "cho hỏi giá mộ đá",
         "created_time": "2026-07-20T10:00:00+0000"},
    ]

    # Act
    out = returning.doi_tin_fb(tin, "PAGE", _fb_time)

    # Assert - cũ trước mới sau, vai đúng
    assert [m["role"] for m in out] == ["user", "assistant"]
    assert out[0]["content"] == "cho hỏi giá mộ đá"
    assert out[0]["at"] == "2026-07-20 10:00:00"


def test_doi_tin_fb_bo_tin_rong():
    # Ảnh/sticker về dưới dạng message rỗng -> bỏ hẳn, không tạo lượt trống
    tin = [{"from": {"id": "KHACH"}, "message": "", "created_time": "2026-07-20T10:00:00+0000"},
           {"from": {"id": "KHACH"}, "message": "  ", "created_time": "2026-07-20T09:00:00+0000"}]
    assert returning.doi_tin_fb(tin, "PAGE", _fb_time) == []


def test_doi_tin_fb_moc_hong_van_giu_noi_dung():
    tin = [{"from": {"id": "KHACH"}, "message": "alo", "created_time": "khong-phai-ngay"}]

    def _no(_):
        raise ValueError("mốc hỏng")

    out = returning.doi_tin_fb(tin, "PAGE", _no)
    assert out == [{"role": "user", "content": "alo"}]     # mất mốc, KHÔNG mất tin


def test_doi_tin_fb_bo_tin_quang_cao_cua_page():
    """Tin hệ thống FB + auto-reply quảng cáo của Page KHÔNG được vào log dưới role assistant:
    model coi đó là văn mẫu của chính nó rồi chép lại vào tin gửi khách (đã xảy ra thật)."""
    chao_ad = ("Chào Bác Luyến! Bác đang quan tâm đến hạng mục Lăng Mộ Đá hay Nhà Thờ Họ ạ ? "
               "Để nắm rõ nhu cầu cũng như mong muốn của bác . Bác gửi em số điện thoại")
    tin = [
        {"from": {"id": "KHACH"}, "message": "Tôi cần tư vấn",
         "created_time": "2026-07-20T10:02:00+0000"},
        {"from": {"id": "PAGE"}, "message": chao_ad, "created_time": "2026-07-20T10:01:00+0000"},
        {"from": {"id": "PAGE"}, "message": "Doàn Luyến replied to an ad.",
         "created_time": "2026-07-20T10:00:00+0000"},
    ]
    out = returning.doi_tin_fb(tin, "PAGE", _fb_time)
    assert out == [{"role": "user", "content": "Tôi cần tư vấn", "at": "2026-07-20 10:02:00"}]


def test_doi_tin_fb_giu_tin_tu_van_that_va_bo_trung_lien_tiep():
    tin = [
        {"from": {"id": "PAGE"}, "message": "Dạ mẫu LD03 khoảng 67 triệu ạ",
         "created_time": "2026-07-20T10:03:00+0000"},
        {"from": {"id": "PAGE"}, "message": "Dạ em chào Bác",
         "created_time": "2026-07-20T10:02:00+0000"},
        {"from": {"id": "PAGE"}, "message": "Dạ em chào Bác",      # gửi trùng liên tiếp
         "created_time": "2026-07-20T10:01:00+0000"},
    ]
    out = returning.doi_tin_fb(tin, "PAGE", _fb_time)
    assert [m["content"] for m in out] == ["Dạ em chào Bác", "Dạ mẫu LD03 khoảng 67 triệu ạ"]


def test_gio_im_lang_lay_moc_cuoi_cung():
    bay_gio = datetime(2026, 7, 27, 12, 0, 0)
    msgs = [{"role": "user", "content": "a", "at": "2026-07-20 12:00:00"},
            {"role": "assistant", "content": "b", "at": "2026-07-25 12:00:00"}]
    assert returning.gio_im_lang(msgs, bay_gio) == 48.0


def test_gio_im_lang_khong_co_moc_tra_am_mot():
    assert returning.gio_im_lang([{"role": "user", "content": "a"}]) == -1.0
    assert returning.gio_im_lang([]) == -1.0
    assert returning.gio_im_lang([{"at": "sai dinh dang"}]) == -1.0


def test_nguong_quay_lai_dung_1_ngay():
    # Luật nghiệp vụ: chỉ báo admin khi khách im HƠN 1 ngày
    assert returning.NGUONG_QUAY_LAI_H == 24.0
    bay_gio = datetime(2026, 7, 27, 12, 0, 0)
    vua_nhan = [{"at": (bay_gio - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")}]
    lau_roi = [{"at": (bay_gio - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S")}]
    assert returning.gio_im_lang(vua_nhan, bay_gio) < returning.NGUONG_QUAY_LAI_H
    assert returning.gio_im_lang(lau_roi, bay_gio) >= returning.NGUONG_QUAY_LAI_H
def test_doi_tin_fb_bo_su_kien_trang_thai_lead_cua_meta():
    tin = [
        {"from": {"id": "PAGE"}, "message": "Lead stage set to Qualified",
         "created_time": "2026-07-20T10:02:00+0000"},
        {"from": {"id": "PAGE"}, "message": "Real advice message",
         "created_time": "2026-07-20T10:01:00+0000"},
    ]

    out = returning.doi_tin_fb(tin, "PAGE", _fb_time)

    assert out == [{"role": "assistant", "content": "Real advice message",
                    "at": "2026-07-20 10:01:00"}]


