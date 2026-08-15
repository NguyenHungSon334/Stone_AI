"""Khách đã cho SĐT thì bot KHÔNG được xin lại.

Lỗi cũ: hồ sơ khách trích từ log CŨ, mà tin khách vừa gửi chỉ tới cuối _answer_sync mới vào
lịch sử -> đúng lượt khách gõ số, ô SĐT trong hồ sơ vẫn trống -> model xin lại ngay câu vừa
nhận số. Cộng thêm mấy câu trả lời CỐ ĐỊNH (không qua model) xin số vô điều kiện.

Chạy: python tests/test_sdt_khong_hoi_lai.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MSGR_APP_SECRET", "s3cret")
os.environ.setdefault("MSGR_VERIFY_TOKEN", "vtok")

import brain
import fb
import messenger
import state
import util

# Test chạy offline: state.get/patch không được chạm Firebase.
fb.fetch_state = lambda psid: None
fb.merge_state = lambda psid, fields: None


def test_tim_sdt_chuan_hoa():
    """Mọi cách khách gõ số đều ra CÙNG một dạng 0xxxxxxxxx (khớp hồ sơ + tra CRM)."""
    for tin in ("0912345678", "0912.345.678", "091 234 5678", "(091) 234-5678",
                "+84912345678", "+84 912 345 678", "84912345678",
                "sdt em 0912345678 nhe", "goi em 0912345678."):
        assert util.tim_sdt(tin) == "0912345678", tin
    assert util.tim_sdt("mo doi 1m55 gia bao nhieu") == ""      # kích thước, không phải SĐT
    assert util.tim_sdt("") == "" and util.tim_sdt(None) == ""


def test_ho_so_thay_sdt_ngay_luot_khach_gui(tmp_path, monkeypatch):
    """REGRESSION: số gõ ở lượt HIỆN TẠI phải vào hồ sơ trước khi dựng prompt."""
    monkeypatch.setattr(state, "_DIR", tmp_path)
    psid = "khach-sdt"
    truoc = {field: "" for field in brain._PROFILE_FIELDS} | {"upto": 4}

    sau = brain._profile_luot_nay(psid, truoc, "Da so em la 0912.345.678 nhe")

    assert sau["sdt"] == "0912345678", "hồ sơ lượt này phải có số khách vừa gõ"
    assert "SĐT/Zalo: 0912345678" in brain._profile_prompt(sau)
    assert state.get(psid)["profile"]["sdt"] == "0912345678", "phải lưu ngay, không chờ bản nền"
    assert sau["upto"] == 4, "không được đụng mốc upto của bản trích nền"


def test_khong_ghi_de_khi_tin_khong_co_so(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_DIR", tmp_path)
    truoc = {"sdt": "0912345678", "upto": 2}
    assert brain._profile_luot_nay("khach-2", truoc, "the lam bao lau ha em") is truoc


def test_ghi_sdt_cho_nhanh_thoat_som(tmp_path, monkeypatch):
    """Handoff cưỡng bức return trước brain.answer -> phải tự ghi số, không thì mất lead."""
    monkeypatch.setattr(state, "_DIR", tmp_path)
    psid = "khach-handoff"
    assert brain.ghi_sdt(psid, util.tim_sdt("0912345678 cho gap nhan vien")) == "0912345678"
    assert brain.sdt_da_luu(psid) == "0912345678"
    assert brain.ghi_sdt(psid, "") == "0912345678", "tin sau không có số thì giữ số cũ"


def test_cau_co_dinh_khong_xin_lai_so():
    """Ba câu không đi qua model: có SĐT rồi thì xác nhận số, không hỏi lại."""
    co_sdt = (brain._cau_xin_lien_he("0912345678"),
              messenger._cau_chuyen_nguoi_that("0912345678"),
              messenger._cau_khach_gui_anh("0912345678"))
    for cau in co_sdt:
        assert "0912345678" in cau
        assert "xin" not in cau.lower() and "để lại" not in cau.lower(), cau

    for cau in (brain._cau_xin_lien_he(""), messenger._cau_chuyen_nguoi_that(""),
                messenger._cau_khach_gui_anh("")):
        assert "xin" in cau.lower() or "để lại" in cau.lower(), "chưa có số thì vẫn phải xin"


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    class _MP:
        def setattr(self, obj, name, val):
            setattr(obj, name, val)

    test_tim_sdt_chuan_hoa()
    test_cau_co_dinh_khong_xin_lai_so()
    with tempfile.TemporaryDirectory() as tmp:
        goc = state._DIR
        try:
            test_ho_so_thay_sdt_ngay_luot_khach_gui(Path(tmp), _MP())
            test_khong_ghi_de_khi_tin_khong_co_so(Path(tmp), _MP())
            test_ghi_sdt_cho_nhanh_thoat_som(Path(tmp), _MP())
        finally:
            state._DIR = goc
    print("sdt khong hoi lai: OK")
