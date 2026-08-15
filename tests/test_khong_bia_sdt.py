"""Bot KHÔNG được bịa SĐT.

Sự cố thật 13/08/2026, khách psid 28632036926390235: khách không hề gõ số nào, bot vẫn nói
"em đã nhận được SĐT của Bác" rồi gửi phiếu ghi "SĐT: 0979655XXX" (số bịa, còn nguyên chữ
XXX), và lead ma đó chui vào Lark CRM.

Ba lớp chặn, test cả ba:
  1. _profile_prompt nói THẲNG là chưa có số (bản cũ chỉ bỏ trường trống ra khỏi prompt).
  2. _chan_bia_sdt chặn tin bịa ngay trước khi gửi.
  3. _extract_lead_sync chỉ lấy SĐT từ chữ số KHÁCH tự gõ, không lấy từ lời bot.

Chạy: python tests/test_khong_bia_sdt.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts
import brain

_TIN_BIA = ("Dạ, em xin chốt lại thông tin báo chuyên gia ạ:\nPHIẾU YÊU CẦU\n"
            "SĐT: 0979655XXX\nNhu cầu: Mộ đơn\nSố lượng: 2 ngôi")


def _tat_alert(monkeypatch):
    monkeypatch.setattr(alerts, "alert", lambda key, text: None)


def test_prompt_noi_ro_chua_co_sdt():
    trong = {field: "" for field in brain._PROFILE_FIELDS} | {"tinh": "Bắc Ninh"}
    prompt = brain._profile_prompt(trong)
    assert "CHƯA CÓ" in prompt and "CẤM" in prompt, "ô SĐT trống phải được nói thẳng"
    assert "Tỉnh/TP: Bắc Ninh" in prompt

    co_so = trong | {"sdt": "0912345678"}
    assert "CHƯA CÓ" not in brain._profile_prompt(co_so)
    assert "SĐT/Zalo: 0912345678" in brain._profile_prompt(co_so)


def test_chan_tin_bia_sdt(monkeypatch):
    _tat_alert(monkeypatch)
    chua_co = {"sdt": ""}

    thay = brain._chan_bia_sdt("psid-1", _TIN_BIA, chua_co)
    assert thay and "0979655" not in thay, "phiếu bịa phải bị thay CẢ tin"
    assert "xin số điện thoại" in thay.lower()

    khang_dinh_sai = "Dạ vâng, em đã nhận được SĐT của Bác. Bên em đang tặng phương án ạ."
    assert brain._chan_bia_sdt("psid-1", khang_dinh_sai, chua_co), "nói dối đã có số cũng phải chặn"


def test_khong_chan_nham(monkeypatch):
    _tat_alert(monkeypatch)
    # Đã có số thật -> phiếu hợp lệ, không được đụng vào.
    assert brain._chan_bia_sdt("psid-2", _TIN_BIA, {"sdt": "0912345678"}) == ""
    # Tin tư vấn bình thường, chưa có số -> vẫn cho qua (bot còn phải đi xin số).
    binh_thuong = "Dạ mộ đơn M04 khoảng 3,3 triệu ạ. Bác cho em xin số điện thoại nhé ạ."
    assert brain._chan_bia_sdt("psid-2", binh_thuong, {"sdt": ""}) == ""


class _Part:
    def __init__(self, text): self.text = text


class _Content:
    def __init__(self, text): self.parts = [_Part(text)]


class _Cand:
    def __init__(self, text): self.content = _Content(text)


class _Resp:
    def __init__(self, text): self.candidates = [_Cand(text)]


def test_lead_khong_lay_sdt_tu_loi_bot(monkeypatch):
    """REGRESSION: convo đưa cho model gồm cả lời bot -> số bot bịa từng được trích ra làm lead."""
    hist = [{"role": "user", "content": "Mua 2 mo don"},
            {"role": "assistant", "content": _TIN_BIA},      # số bịa nằm ở lời BOT
            {"role": "user", "content": "Ok"}]
    lead_model_tra = json.dumps({"ten": "Han Nguyen Tien", "sdt": "0979655XXX",
                                 "dia_chi": "Bắc Ninh", "tinh": "Bắc Ninh",
                                 "khu_vuc": "Miền Bắc", "tom_tat": "2 mộ đơn"})
    monkeypatch.setattr(brain, "_load_hist", lambda psid: hist)
    monkeypatch.setattr(brain, "_profile_from_history_sync", lambda psid, h: {"sdt": ""})
    monkeypatch.setattr(brain, "_get_client", lambda: None)
    monkeypatch.setattr(brain, "_generate", lambda *a, **kw: _Resp(lead_model_tra))

    assert brain._extract_lead_sync("psid-3") is None, "khách chưa gõ số -> KHÔNG được tạo lead"

    hist.insert(1, {"role": "user", "content": "so em 0912.345.678"})
    lead = brain._extract_lead_sync("psid-3")
    assert lead is not None and lead["sdt"] == "0912345678", "phải lấy đúng số khách tự gõ"
    assert lead["tinh"] == "Bắc Ninh"


if __name__ == "__main__":
    class _MP:
        def setattr(self, obj, name, val): setattr(obj, name, val)

    goc = (alerts.alert, brain._load_hist, brain._profile_from_history_sync,
           brain._get_client, brain._generate)
    try:
        test_prompt_noi_ro_chua_co_sdt()
        test_chan_tin_bia_sdt(_MP())
        test_khong_chan_nham(_MP())
        test_lead_khong_lay_sdt_tu_loi_bot(_MP())
    finally:
        (alerts.alert, brain._load_hist, brain._profile_from_history_sync,
         brain._get_client, brain._generate) = goc
    print("khong bia sdt: OK")
