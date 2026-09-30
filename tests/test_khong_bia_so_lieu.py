"""Bot KHÔNG được bịa giá / kích thước / trọng lượng / mã.

Sự cố thật 28/09/2026, khách Trần Long (psid 38761704246810866): bot báo "HR01 Dài 1000 x Cao
810mm khoảng 1.8 triệu mỗi mét dài" - bảng ghi 4tr/md xanh đen, không có chiều 'Dài 1000'.

Hai lớp, test cả hai:
  1. kiem_so_lieu.kiem bắt số lệch bảng hàng (giữ nguyên số đúng).
  2. _answer_sync bắt model viết lại 1 lần kèm số thật; vẫn sai thì cắt câu sai.
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts
import brain
from bot_tools.find_by_price import rows_by_ids
from bot_tools.kiem_so_lieu import cat_cau_sai, kiem


def _gia_xanh_den(ma: str, phay: bool = False) -> str:
    """Giá xanh đen THẬT hiện tại trong bảng, dạng bot hay viết ('82.2 triệu'). Không viết cứng:
    sync bảng hàng từ Lark là giá đổi, test viết cứng sẽ gãy dù bộ kiểm vẫn đúng."""
    s = f"{rows_by_ids([ma], 'xanh đen')[0][0] / 1e6:.1f} triệu"
    return s.replace(".", ",") if phay else s


_GIA_LD10 = _gia_xanh_den("LĐ10")
_TIN_TRAN_LONG = (
    "Dạ em báo giá tham khảo ạ:\n\n"
    f"LĐ10 có kích thước Dài 1470 x Rộng 970 x Cao 3400mm có giá khoảng {_GIA_LD10} với đá xanh đen.\n\n"
    "Tường rào khoảng 37,4 mét dài. Em gợi ý mẫu HR01 có kích thước Dài 1000 x Cao 810mm có giá "
    "khoảng 1.8 triệu mỗi mét dài với đá xanh đen. Mẫu này thanh thoát.\n\n"
    "Anh dự kiến mấy ngôi ạ?")


def test_bat_gia_va_kich_thuoc_bia():
    loi = kiem(_TIN_TRAN_LONG, [])
    assert any("1.8 triệu" in x and "HR01" in x for x in loi)
    assert any("'1000" in x for x in loi)
    assert not any("LĐ10" in x for x in loi), "số đúng của LĐ10 không được bắt oan"


def test_so_dung_bang_hang_thi_sach():
    tin = ("HR01 cao 810mm, giá khoảng 4 tới 5 triệu mỗi mét dài tuỳ loại đá. "
           "37,4 mét dài xanh đen tạm tính khoảng 149.6 triệu.")
    assert kiem(tin, []) == [], "giá đúng + giá x số lượng phải qua"


def test_so_khach_tu_noi_khong_bi_bat():
    tin = "Ngân sách 100 triệu của Bác làm được ạ, kích thước 1070x1670mm bên em nhận làm riêng."
    assert kiem(tin, ["tầm 100 triệu", "kích thước 1,07m× 1,67"]) == []


def test_ma_sai_chinh_ta():
    loi = kiem("Dạ em thấy đây là mẫu Long đình LD13 ạ.", [])
    assert loi and "LĐ13" in loi[0]


def test_cat_cau_sai_giu_cau_dung():
    sach = cat_cau_sai(_TIN_TRAN_LONG, [])
    assert "1.8" not in sach and "1000" not in sach
    assert _GIA_LD10 in sach and "Mẫu này thanh thoát." in sach and "\n\n" in sach


def _fake_resp(text):
    cand = SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text=text, function_call=None)]),
                           finish_reason="STOP")
    return SimpleNamespace(candidates=[cand], usage_metadata=None, prompt_feedback=None)


def _chay(monkeypatch, tmp_path, cac_ban_nhap):
    """Chạy _answer_sync với model giả trả lần lượt các bản nháp. Trả (tin gửi, prompts)."""
    monkeypatch.setattr(brain, "_HIST_DIR", tmp_path)
    monkeypatch.setattr(brain, "_get_client", lambda: object())
    monkeypatch.setattr(brain, "_cached_handle", lambda c, m: None)
    monkeypatch.setattr(brain, "_refresh_profile_bg", lambda *a: None)
    monkeypatch.setattr(brain, "_summarize_bg", lambda *a: None)
    monkeypatch.setattr(brain, "_image_markers", lambda *a: "")
    monkeypatch.setattr(brain.stats, "log_usage", lambda *a: None)
    canh_bao = []
    monkeypatch.setattr(alerts, "alert", lambda key, text: canh_bao.append(text))
    nhap, prompts = iter(cac_ban_nhap), []

    def gen(client, model_id, contents, handle):
        prompts.append(list(contents))
        return _fake_resp(next(nhap)), handle
    monkeypatch.setattr(brain, "_gen_answer", gen)
    return brain._answer_sync("PSID_TEST", "báo giá tổng cả khu"), prompts, canh_bao


def test_nhap_bia_thi_bat_viet_lai_kem_so_that(monkeypatch, tmp_path):
    dung = "HR01 cao 810mm giá khoảng 4 triệu mỗi mét dài với đá xanh đen ạ."
    tin, prompts, canh_bao = _chay(monkeypatch, tmp_path, [_TIN_TRAN_LONG, dung])

    assert tin == dung
    lenh = prompts[1][-1].parts[0].text
    assert "KIỂM TRA SỐ LIỆU" in lenh and "HR01" in lenh and "4tr" in lenh, "phải kèm số thật"
    assert not canh_bao


def test_viet_lai_van_bia_thi_cat_cau_va_bao_admin(monkeypatch, tmp_path):
    tin, _, canh_bao = _chay(monkeypatch, tmp_path, [_TIN_TRAN_LONG, _TIN_TRAN_LONG])

    assert "1.8" not in tin and "1000" not in tin
    assert _GIA_LD10 in tin and brain._CAU_HEN_BAO_GIA in tin
    assert canh_bao and "BỊA SỐ LIỆU" in canh_bao[0]


def test_gia_phai_dung_loai_da():
    """5tr là giá xanh RÊU của HR01 - đọc thành xanh đen là sai dù con số có trong bảng."""
    assert kiem("HR01 giá khoảng 5 triệu mỗi mét dài với đá xanh đen.", [])
    assert kiem("HR01 xanh đen khoảng 4 triệu, xanh rêu khoảng 5 triệu mỗi mét dài.", []) == []
    assert kiem("HR01 khoảng 4 tới 5 triệu mỗi mét dài tuỳ loại đá.", []) == []
    assert kiem(f"Cổng C08 cao 1m39, giá khoảng {_gia_xanh_den('C08', phay=True)} mỗi bộ.", []) == [], "6,5 là số thập phân"
    assert kiem(f"Mẫu LĐ10 rất trang trọng, giá khoảng {_GIA_LD10}.", []) == [], "'trang trọng' không phải đá trắng"


def test_kich_thuoc_nho_va_cm_va_met():
    assert kiem("Cổng C08 thân cột 200x200mm.", []), "cỡ dưới 300mm cũng phải kiểm"
    assert kiem("Mộ M04 kích thước 81x127cm.", []), "cm của mã cụ thể quy ra mm để kiểm"
    assert kiem("Mộ M07 kích thước 81x127cm.", []) == []
    assert kiem("Kích thước phổ biến là 81x127cm theo Lỗ Ban.", []) == [], "câu không mã = kiến thức chung"
    assert kiem("Cổng C08 cao 1m86.", []) and kiem("Cổng C08 cao 1m39.", []) == []


def test_dac_diem_chu_phai_khop_bang():
    assert kiem("Cổng M04 rất đẹp.", []), "sai thể loại"
    assert kiem("LĐ04 là Long đình 3 mái.", []), "LĐ04 là 1 mái"
    assert kiem("LĐ04 là Long đình 3 mái.", ["em cần long đình 3 mái"]) == [], "nhắc lại lời khách thì được"
    assert kiem("C08 là cổng tứ trụ.", []), "C08 là 2 cột"
    assert kiem("HR01 giá 4 triệu mỗi ngôi.", []), "hàng rào tính theo mét dài"
    assert kiem("LĐ10 thuộc dòng Trường Tồn.", []), "LĐ10 là An Tâm"
    assert kiem("M04 là mẫu bán chạy.", []), "M04 không gắn nhãn bán chạy"


def test_khach_noi_so_le_bang_chu():
    """Khách Trần Long gõ 'Bình Phong rộng khoảng 1 m tư' -> bot ghi '1m4' không phải bịa."""
    tin = "Long đình LĐ13, bình phong rộng khoảng 1m4 làm riêng."
    assert kiem(tin, ["Bình Phong rộng khoảng 1 m tư"]) == []
    assert kiem(tin, [])
