"""CRM Lark phải theo kịp hội thoại. Sự cố 18/08/2026: khách trả lời tiếp trên Messenger
nhưng bảng Lark đứng yên ở trạng thái lúc vừa nhận SĐT.

Năm đường mất cập nhật, test cả năm:
  1. Chỉ ghi CRM ở 4 mốc (handoff/pause/SĐT trong CHÍNH tin đó) -> lượt sau khách nói địa
     chỉ/tỉnh không lượt nào chạm Lark.
  2. _save_meta chụp fields MONG MUỐN thay vì fields Lark ĐÃ NHẬN -> cột cascade bị từ chối
     vẫn bị coi là đã đồng bộ, trống vĩnh viễn.
  3. Value không khớp option ("TP Hà Nội" vs option "Hà Nội") -> bỏ cột, im lặng.
  4. Khách đang bị khoá 24h quay lại cho SĐT -> _noise_decision trả "blocked", thoát trước
     mọi nhánh CRM, lead rơi vào hư không.
  5. AI tóm tắt hỏng (JSON cụt do chat dài) -> nuốt luôn cả lead.

Chạy: python tests/test_crm_dong_bo.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts
import brain
from bot_tools import lark_crm

_OPTIONS = {"Khu vực": {"Miền Bắc", "Miền Trung", "Miền Nam"},
            "Tỉnh/Thành phố": {"Hà Nội", "Nghệ An"},
            "Nguồn lead": {"Facebook"}}


class _Resp:
    """Bản trả lời Gemini giả, đủ hình dạng resp.candidates[0].content.parts[i].text."""

    def __init__(self, text):
        part = type("P", (), {"text": text})()
        content = type("C", (), {"parts": [part]})()
        self.candidates = [type("Cd", (), {"content": content})()]


def _tat_mang(monkeypatch):
    monkeypatch.setattr(alerts, "alert", lambda key, text: None)
    monkeypatch.setattr(lark_crm, "_select_options", lambda col: _OPTIONS.get(col, set()))
    monkeypatch.setattr(lark_crm, "_tenant_token", lambda: "tok")
    monkeypatch.setattr(lark_crm, "_fetch_lead_code", lambda *a, **kw: "L-1")
    monkeypatch.setattr(lark_crm, "_record_exists", lambda rid, tok: True)


# --- (3) khớp option khoan dung ---------------------------------------------------------

def test_option_khop_du_co_tien_to(monkeypatch):
    _tat_mang(monkeypatch)
    assert lark_crm._match_option("TP Hà Nội", "Tỉnh/Thành phố") == "Hà Nội"
    assert lark_crm._match_option("tỉnh nghệ an", "Tỉnh/Thành phố") == "Nghệ An"
    assert lark_crm._match_option("Hà Nội", "Tỉnh/Thành phố") == "Hà Nội"


def test_option_khong_khop_thi_bao_admin(monkeypatch):
    _tat_mang(monkeypatch)
    keu = []
    monkeypatch.setattr(alerts, "alert", lambda key, text: keu.append(key))
    assert lark_crm._match_option("Vientiane", "Tỉnh/Thành phố") == ""
    assert keu, "bỏ cột mà không báo = khách nói tỉnh nhưng bảng trắng, không ai truy được"
    keu.clear()
    assert lark_crm._match_option("", "Tỉnh/Thành phố") == "" and not keu, "trống thì đừng kêu"


# --- (2) snapshot phải là fields Lark ĐÃ NHẬN -------------------------------------------

def _gia_lap_lark(monkeypatch, tu_choi_cascade: bool):
    """Thay request_retry: trả lỗi cascade cho lần ghi có cột Tỉnh/Thành phố. Ghi lại các call."""
    calls = []

    def fake(method, url, headers=None, json=None, timeout=None, params=None):
        fields = (json or {}).get("fields", {})
        calls.append(dict(fields))
        loi = tu_choi_cascade and "Tỉnh/Thành phố" in fields
        body = ({"code": lark_crm._ERR_SELECT_CONV, "msg": "SingleSelectFieldConvFail"} if loi
                else {"code": 0, "data": {"record": {"record_id": "rec1"}}})
        return type("R", (), {"json": lambda self, b=body: b})()

    monkeypatch.setattr(lark_crm, "request_retry", fake)
    return calls


def test_cot_bi_tu_choi_khong_bi_coi_la_da_ghi(monkeypatch, tmp_path):
    """REGRESSION: cột cascade bị bỏ mà snapshot vẫn ghi -> lượt sau 'unchanged', trống mãi."""
    _tat_mang(monkeypatch)
    monkeypatch.setattr(lark_crm, "_META_DIR", tmp_path)
    calls = _gia_lap_lark(monkeypatch, tu_choi_cascade=True)

    lead = {"sdt": "0912345678", "ten": "A", "dia_chi": "Số 1", "tinh": "Hà Nội",
            "khu_vuc": "Miền Bắc", "tom_tat": "mộ đơn"}
    assert lark_crm.upsert_lead("p1", lead) == "created"

    meta = lark_crm._load_meta("p1")
    assert "Tỉnh/Thành phố" not in meta["fields"], "snapshot phải là fields Lark THẬT SỰ nhận"
    assert meta["tu_choi"] == ["Tỉnh/Thành phố"]
    assert meta["fields"]["Địa chỉ"] == "Số 1"

    # Không có gì mới -> KHÔNG ghi lại, không báo admin (dù snapshot thiếu cột cascade).
    calls.clear()
    assert lark_crm.upsert_lead("p1", lead) == "unchanged"
    assert calls == [], "cột bị từ chối không được làm bot ghi lại Lark mỗi lượt"

    # Có thay đổi thật -> ghi lại, và THỬ LẠI cả cột cascade (admin sửa Base xong là tự vào).
    lark_crm.upsert_lead("p1", {**lead, "dia_chi": "Số 2"})
    assert "Tỉnh/Thành phố" in calls[0], "mỗi lần ghi thật phải thử lại cột cascade"


def test_dong_bo_re_khong_xoa_ghi_chu(monkeypatch, tmp_path):
    """Lượt đồng bộ rẻ không có tom_tat -> ô Ghi chú phải giữ nguyên, không nhấp nháy."""
    _tat_mang(monkeypatch)
    monkeypatch.setattr(lark_crm, "_META_DIR", tmp_path)
    calls = _gia_lap_lark(monkeypatch, tu_choi_cascade=False)

    lark_crm.upsert_lead("p2", {"sdt": "0912345678", "ten": "A", "tom_tat": "mộ đơn 2 ngôi"})
    calls.clear()
    # Lượt sau: khách nói địa chỉ, không tóm tắt lại -> lead KHÔNG có key tom_tat.
    assert lark_crm.upsert_lead("p2", {"sdt": "0912345678", "ten": "A", "dia_chi": "Số 1"}) == "updated"
    assert calls[0]["Ghi chú"] == "mộ đơn 2 ngôi", "đồng bộ rẻ không được làm mất Ghi chú"
    assert calls[0]["Địa chỉ"] == "Số 1"
    calls.clear()
    assert lark_crm.upsert_lead("p2", {"sdt": "0912345678", "ten": "A", "dia_chi": "Số 1"}) == "unchanged"
    assert calls == [], "lặp lại y hệt thì đừng ghi Lark nữa"


# --- (1) đồng bộ rẻ mỗi lượt + (5) AI hỏng vẫn ra lead -----------------------------------

def _hist(n_user: int):
    return [{"role": "user", "content": f"tin {i} so 0912345678" if i == 0 else f"tin {i}"}
            for i in range(n_user)]


def test_dong_bo_re_lay_ho_so_khong_goi_ai(monkeypatch):
    """deep=False và log chưa dài thêm -> KHÔNG tốn AI, nhưng địa chỉ/tỉnh mới vẫn ra."""
    _tat_mang(monkeypatch)
    hist = _hist(10)
    monkeypatch.setattr(brain, "_load_hist", lambda psid: hist)
    monkeypatch.setattr(brain, "_profile_from_history_sync",
                        lambda psid, h: {"sdt": "0912345678", "ten": "A", "dia_chi": "Xóm 3",
                                         "tinh": "Nghệ An", "khu_vuc": "Miền Trung"})
    monkeypatch.setattr(brain, "_generate", _no_ai)

    lead = brain._extract_lead_sync("p3", deep=False, tom_tat_toi=len(hist))
    assert lead["dia_chi"] == "Xóm 3" and lead["tinh"] == "Nghệ An"
    assert "tom_tat" not in lead, "đồng bộ rẻ không tóm tắt lại -> giữ Ghi chú cũ trên Lark"
    assert lead["n_msg"] == len(hist)


def _no_ai(*a, **kw):
    raise AssertionError("đồng bộ rẻ không được gọi AI")


def test_log_dai_them_thi_tom_tat_lai(monkeypatch):
    _tat_mang(monkeypatch)
    hist = _hist(10)
    monkeypatch.setattr(brain, "_load_hist", lambda psid: hist)
    monkeypatch.setattr(brain, "_profile_from_history_sync",
                        lambda psid, h: {"sdt": "0912345678", "ten": "", "dia_chi": "",
                                         "tinh": "", "khu_vuc": ""})
    monkeypatch.setattr(brain, "_get_client", lambda: None)
    monkeypatch.setattr(brain, "_generate", lambda *a, **kw: _Resp(json.dumps(
        {"ten": "B", "sdt": "0000", "dia_chi": "Xóm 5", "tinh": "Nghệ An",
         "khu_vuc": "Miền Trung", "tom_tat": "2 mộ đơn"})))

    lead = brain._extract_lead_sync("p4", deep=False, tom_tat_toi=len(hist) - brain._LEAD_TOM_TAT_MOI)
    assert lead["tom_tat"] == "2 mộ đơn" and lead["tom_tat_toi"] == len(hist)
    assert lead["dia_chi"] == "Xóm 5", "hồ sơ trống thì mới lấy bản AI lấp vào"
    assert lead["sdt"] == "0912345678", "SĐT vẫn chỉ lấy từ chữ số KHÁCH gõ, không lấy của AI"


def test_ai_hong_van_ra_lead(monkeypatch):
    """REGRESSION: chat dài -> JSON cụt -> bản cũ return None, nuốt luôn lead đã có SĐT."""
    _tat_mang(monkeypatch)
    hist = _hist(10)
    monkeypatch.setattr(brain, "_load_hist", lambda psid: hist)
    monkeypatch.setattr(brain, "_profile_from_history_sync",
                        lambda psid, h: {"sdt": "0912345678", "ten": "A", "dia_chi": "Xóm 3",
                                         "tinh": "Nghệ An", "khu_vuc": "Miền Trung"})
    monkeypatch.setattr(brain, "_get_client", lambda: None)
    monkeypatch.setattr(brain, "_generate", lambda *a, **kw: _Resp('{"ten": "A", "tom'))

    lead = brain._extract_lead_sync("p5", deep=True)
    assert lead is not None, "tóm tắt hỏng chỉ được mất ô Ghi chú, KHÔNG được mất cả lead"
    assert lead["sdt"] == "0912345678" and lead["dia_chi"] == "Xóm 3"
    assert "tom_tat" not in lead


def test_prompt_tom_tat_bi_chan_do_dai(monkeypatch):
    """Chat dài -> chỉ nhồi _LEAD_CONVO_TIN tin cuối, không nhồi cả log."""
    _tat_mang(monkeypatch)
    thay = {}
    monkeypatch.setattr(brain, "_get_client", lambda: None)

    def bat(client, *a, **kw):
        thay["prompt"] = kw["contents"][0].parts[0].text
        return _Resp(json.dumps({"ten": "", "sdt": "", "dia_chi": "", "tinh": "",
                                 "khu_vuc": "", "tom_tat": "ok"}))

    monkeypatch.setattr(brain, "_generate", bat)
    brain._tom_tat_lead("p6", [{"role": "user", "content": f"tin{i}"} for i in range(500)])
    assert "tin0:" not in thay["prompt"] and "tin499" in thay["prompt"]
    assert thay["prompt"].count("Khách: tin") == brain._LEAD_CONVO_TIN


# --- (4) khách đang bị khoá cho SĐT vẫn phải lên CRM -------------------------------------

def test_khach_bi_khoa_cho_sdt_van_vao_crm(monkeypatch):
    """REGRESSION: _noise_decision trả 'blocked' -> bản cũ return trước mọi nhánh CRM."""
    _tat_mang(monkeypatch)
    import messenger

    da_ghi = []

    async def ghi_crm(psid, deep=True):
        da_ghi.append((psid, deep))

    monkeypatch.setattr(messenger, "_noise_decision", lambda psid, text: ("blocked", {}))
    monkeypatch.setattr(messenger, "_save_lead_to_crm", ghi_crm)
    monkeypatch.setattr(brain, "ghi_sdt", lambda psid, sdt: da_ghi.append(("sdt", sdt)))

    import asyncio
    asyncio.run(messenger._process_inner("p7", "em gui lai so 0912345678 nhe"))

    assert ("sdt", "0912345678") in da_ghi, "tin không vào log -> phải ghi số thẳng vào hồ sơ"
    assert any(x[0] == "p7" for x in da_ghi), "khách bị khoá cho số vẫn phải lên Lark"


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
