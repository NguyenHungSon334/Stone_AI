"""Self-check: khách bấm quảng cáo -> bot mở lời trước.
Khách mới nhận câu chào cố định; khách cũ để AI mở lời tiếp nối; bấm dồn thì im.
Chạy: python tests/test_referral.py"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain
import messenger
import returning


def _chay(psid: str, hist: list, quay_lai: bool = False) -> dict:
    """Chạy 1 lượt referral với lịch sử cho sẵn. Trả về những gì bot đã làm.

    quay_lai: giả lập khách im >=24h rồi mới bấm quảng cáo lại."""
    ghi = {"sent": [], "ai_prompt": None, "logged": [], "crm": []}

    async def _load(_psid):
        return hist

    async def _send(_psid, txt, **kw):
        ghi["sent"].append(txt)

    async def _answer(_psid, txt, *a, **kw):
        ghi["ai_prompt"] = txt
        return "Dạ em chào Bác ạ."

    async def _noop(*a, **kw):
        return None

    async def _quay_lai(*a, **kw):
        return quay_lai

    async def _save_crm(_psid, deep=True):
        ghi["crm"].append((_psid, deep))

    async def _ghi_luot(_psid, user_text, bot_text, *a, **kw):
        ghi["logged"].append((user_text, bot_text))

    # Chặn 2 tác dụng phụ ra ngoài: gọi Graph API (typing) và báo admin/ghi mark khách quay lại.
    orig = (brain.load_history_async, brain.answer, brain.ghi_luot_async, messenger.send_text,
            messenger.send_action, returning.xu_ly_khach_quay_lai, messenger._save_lead_to_crm)
    brain.load_history_async, brain.answer, messenger.send_text = _load, _answer, _send
    brain.ghi_luot_async = _ghi_luot          # KHÔNG cho test ghi thật vào conversations/
    messenger.send_action = _noop
    returning.xu_ly_khach_quay_lai = _quay_lai
    messenger._save_lead_to_crm = _save_crm   # KHÔNG cho test gọi Lark thật
    try:
        asyncio.run(messenger._process_inner(psid, messenger._REFERRAL_EVENT))
    finally:
        (brain.load_history_async, brain.answer, brain.ghi_luot_async, messenger.send_text,
         messenger.send_action, returning.xu_ly_khach_quay_lai, messenger._save_lead_to_crm) = orig
    return ghi


def test_khach_moi_nhan_cau_chao_co_dinh():
    """Chưa có lịch sử -> câu chào cố định, KHÔNG gọi AI (khỏi tốn token cho 1 câu chào)."""
    ghi = _chay("PSID_MOI", [])
    assert ghi["sent"] == [messenger._REFERRAL_REPLY]
    assert ghi["ai_prompt"] is None
    # Câu chào PHẢI vào log: không ghi thì lượt sau model tưởng mình chưa nói gì -> khách đáp
    # số điện thoại xong bị hỏi lại, và bấm ad lần 2 lại nhận đúng câu chào đó lần nữa.
    assert ghi["logged"] == [("", messenger._REFERRAL_REPLY)]


def test_khach_cu_thi_ai_mo_loi_tiep_noi():
    """Đã có lịch sử và im lâu -> đẩy vào AI kèm ngữ cảnh, không chào trơ như người lạ."""
    hist = [{"role": "user", "content": "Khu lăng mộ 52m", "at": "2026-07-20 10:00:00"},
            {"role": "assistant", "content": "Dạ khu đó...", "at": "2026-07-20 10:01:00"}]
    ghi = _chay("PSID_CU", hist)
    assert ghi["ai_prompt"] == messenger._REFERRAL_PROMPT
    assert messenger._REFERRAL_REPLY not in ghi["sent"]


def test_bam_ad_don_thi_im():
    """Bot vừa nhắn xong mà khách bấm ad tiếp -> không nhắn chồng lên tin chưa ai đọc."""
    from datetime import datetime, timedelta
    vua_xong = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    hist = [{"role": "assistant", "content": "Dạ em chào Bác ạ.", "at": vua_xong}]
    ghi = _chay("PSID_DON", hist)
    assert ghi["sent"] == [] and ghi["ai_prompt"] is None


def test_khach_quay_lai_bam_ad_van_cap_nhat_crm():
    """REGRESSION: nhánh referral thoát sớm ở nhiều chỗ, KHÔNG chỗ nào chạm CRM.

    Khách cũ im mấy ngày rồi bấm lại quảng cáo = tín hiệu quan tâm lại rõ nhất, mà ô Ghi chú
    trên Lark đứng yên ở trạng thái lần chốt trước."""
    from datetime import datetime, timedelta
    vua_xong = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    hist = [{"role": "assistant", "content": "Dạ em chào Bác ạ.", "at": vua_xong}]

    # Nhánh im lặng (bấm ad dồn) - thoát sớm nhất, vẫn phải cập nhật CRM.
    ghi = _chay("PSID_QL", hist, quay_lai=True)
    assert ghi["crm"] == [("PSID_QL", True)], "khách quay lại bấm ad phải tóm tắt lại Ghi chú"
    assert ghi["sent"] == [], "vẫn không nhắn chồng lên tin bot vừa gửi"

    # Đang trò chuyện liên tục -> không tốn thêm lượt AI tóm tắt.
    assert _chay("PSID_LT", hist, quay_lai=False)["crm"] == []


if __name__ == "__main__":
    test_khach_moi_nhan_cau_chao_co_dinh()
    test_khach_cu_thi_ai_mo_loi_tiep_noi()
    test_bam_ad_don_thi_im()
    test_khach_quay_lai_bam_ad_van_cap_nhat_crm()
    print("OK - referral")
