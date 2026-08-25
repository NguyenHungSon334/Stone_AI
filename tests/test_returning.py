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
    """Tin hệ thống FB bị bỏ. Auto-reply quảng cáo thì THAY bằng ghi chú, không bỏ hẳn:

    - bỏ CHỮ quảng cáo: model coi đó là văn mẫu của chính nó rồi chép lại cho khách (đã xảy ra).
    - giữ DẤU VẾT "Page đã xin số": câu ad chính là câu xin SĐT. Bỏ hẳn thì khách bấm ad, gõ số
      ra, log chỉ còn dãy số trơ không câu hỏi nào -> bot chào lại như người lạ rồi XIN SỐ LẦN
      NỮA (khách 27234963929538234, 28/07/2026).
    """
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
    assert out == [
        {"role": "assistant", "content": returning._CHAO_QUANG_CAO_NOTE, "at": "2026-07-20 10:01:00"},
        {"role": "user", "content": "Tôi cần tư vấn", "at": "2026-07-20 10:02:00"},
    ]
    assert "quan tâm đến hạng mục" not in returning._CHAO_QUANG_CAO_NOTE


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




def test_gio_im_lang_tach_theo_role():
    """REGRESSION: tin BOT tự gửi không được reset đồng hồ im lặng của KHÁCH.

    Bot nhắc khách im lâu (_FOLLOWUP_TEXT) -> tin đó nay vào log. Nếu gio_im_lang lấy tin cuối
    bất kể của ai thì khách đáp lại lời nhắc bị tính là "im 2 giờ" -> mất thông báo
    🔁 KHÁCH QUAN TÂM LẠI. Ngược lại nhánh referral cần đúng mốc tin BOT."""
    def moc(gio):
        return (datetime.now() - timedelta(hours=gio)).strftime("%Y-%m-%d %H:%M:%S")

    msgs = [{"role": "user", "content": "xin giá", "at": moc(30)},
            {"role": "assistant", "content": "Dạ 33 triệu ạ", "at": moc(29)},
            {"role": "assistant", "content": "Bác còn phân vân mẫu nào không ạ?", "at": moc(1)}]

    assert 29 <= returning.gio_im_lang(msgs, role="user") <= 31, "khách im ~30h"
    assert returning.gio_im_lang(msgs, role="assistant") < 2, "bot vừa nhắn ~1h trước"
    assert returning.gio_im_lang(msgs) < 2, "mặc định vẫn là tin cuối bất kể role"
    assert returning.gio_im_lang([{"role": "assistant", "at": moc(1)}], role="user") == -1.0


def test_quay_lai_tra_true_de_ep_tom_tat_lai_ghi_chu(monkeypatch):
    """REGRESSION: khách im lâu rồi nhắn lại mà ô Ghi chú CRM đứng yên.

    1 tin khách = log dài thêm 2, dưới ngưỡng brain._LEAD_TOM_TAT_MOI -> đồng bộ rẻ giữ Ghi
    chú cũ, upsert trả 'unchanged', Lark không nhúc nhích. messenger dùng cờ này ép deep."""
    import asyncio

    import messenger

    def moc(gio):
        return (datetime.now() - timedelta(hours=gio)).strftime("%Y-%m-%d %H:%M:%S")

    hist = [{"role": "user", "content": "cho hỏi giá", "at": moc(72)},
            {"role": "assistant", "content": "Dạ", "at": moc(71)}]
    monkeypatch.setattr(returning.brain, "load_history_async", _tra_ve(hist))
    monkeypatch.setattr(returning, "_tom_tat_crm", lambda psid: "")
    monkeypatch.setattr(returning, "_da_bao", lambda psid, m: False)
    monkeypatch.setattr(returning, "_ghi_mark", lambda psid, m: None)
    monkeypatch.setattr(messenger, "_label", _tra_ve("Khách A"))
    monkeypatch.setattr(messenger, "notify_admins", _tra_ve(None))

    assert asyncio.run(returning.xu_ly_khach_quay_lai("p1")) is True, "im 72h = quay lại"

    # Đang trò chuyện liên tục -> KHÔNG ép deep (mỗi lần deep tốn 1 lượt AI tóm tắt).
    lien_tuc = [{"role": "user", "content": "vâng", "at": moc(1)}]
    monkeypatch.setattr(returning.brain, "load_history_async", _tra_ve(lien_tuc))
    assert asyncio.run(returning.xu_ly_khach_quay_lai("p1")) is False

    # Chưa có log và Graph không trả gì -> khách mới, không phải quay lại.
    monkeypatch.setattr(returning.brain, "load_history_async", _tra_ve([]))
    monkeypatch.setattr(returning, "_keo_lich_su_cu", _tra_ve([]))
    assert asyncio.run(returning.xu_ly_khach_quay_lai("p1")) is False


def _tra_ve(gia_tri):
    """Coroutine giả luôn trả `gia_tri` (nuốt mọi tham số)."""
    async def _f(*a, **kw):
        return gia_tri
    return _f
