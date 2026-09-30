"""Khách đến từ QUẢNG CÁO phải lên CRM là 'Quảng cáo Facebook', không lẫn với khách tự nhắn Page.

Ca thật 28/09/2026, khách Trần Long (psid 38761704246810866): bấm quảng cáo linh vật đá
("Trần Long replied to an ad"), CRM vẫn ghi Nguồn lead = "Facebook" như mọi khách khác.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts
import messenger
import state
from bot_tools import lark_crm


def _payload(ev: dict) -> dict:
    return {"object": "page", "entry": [{"messaging": [{"sender": {"id": "P_QC"}, **ev}]}]}


def test_bam_quang_cao_thi_luu_co_va_ad_id():
    messenger.parse_events(_payload({"referral": {"source": "ADS", "ad_id": "123", "type": "OPEN_THREAD"}}))
    s = state.get("P_QC")
    assert s.get("tu_quang_cao") is True and s.get("ad_id") == "123"


def test_referral_kem_tin_nhan_dau_cung_bat_duoc():
    messenger.parse_events(_payload({"message": {"text": "tư vấn giúp",
                                                 "referral": {"source": "ADS", "ad_id": "9"}}}))
    assert state.get("P_QC").get("tu_quang_cao") is True


def test_link_mme_khong_phai_quang_cao():
    messenger.parse_events(_payload({"referral": {"source": "SHORTLINK", "ref": "abc"}}))
    assert not state.get("P_QC").get("tu_quang_cao")


def test_crm_ghi_dung_nguon(monkeypatch):
    monkeypatch.setattr(alerts, "alert", lambda key, text: None)
    monkeypatch.setattr(lark_crm, "_select_options",
                        lambda col: {"Facebook", "Quảng cáo Facebook"} if col == "Nguồn lead" else set())

    assert lark_crm._build_fields({"tu_quang_cao": True})["Nguồn lead"] == "Quảng cáo Facebook"
    assert lark_crm._build_fields({})["Nguồn lead"] == "Facebook"


def test_bang_chua_co_option_thi_ghi_tam_facebook_va_bao_admin(monkeypatch):
    keu = []
    monkeypatch.setattr(alerts, "alert", lambda key, text: keu.append(text))
    monkeypatch.setattr(lark_crm, "_select_options", lambda col: {"Facebook"})

    assert lark_crm._build_fields({"tu_quang_cao": True})["Nguồn lead"] == "Facebook"
    assert keu and "Quảng cáo Facebook" in keu[0]
