import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import brain
import messenger
import state


def test_noise_guard_stops_then_reopens_for_meaningful_text(monkeypatch, tmp_path):
    monkeypatch.setattr(brain, "_HIST_DIR", tmp_path)

    assert messenger._noise_decision("p1", "o")[0] == "clarify"
    assert messenger._noise_decision("p1", "ommo")[0] == "stop"
    assert messenger._noise_decision("p1", "o")[0] == "blocked"
    assert messenger._noise_decision("p1", "mo da gia bao nhieu")[0] == "allow"
    assert messenger._noise_decision("p1", "o")[0] == "clarify"


def test_khach_bi_ngung_tra_loi_khong_bi_bao_tin_roi_lap(monkeypatch, tmp_path):
    """Khách đã bị bot ngưng trả lời -> vòng quét tin rơi phải im, không báo mỗi chu kỳ."""
    monkeypatch.setattr(brain, "_HIST_DIR", tmp_path)
    messenger._noise_decision("p9", "o")
    messenger._noise_decision("p9", "ommo")          # -> stopped
    assert messenger._noise_state("p9").get("stopped")

    at = "2026-07-29T03:00:00+0000"
    assert not brain.missed_already_reported("p9", at)

    # mô phỏng đúng nhánh của run_missed_check
    assert messenger._noise_state("p9").get("stopped")
    brain.mark_missed_reported("p9", at)

    assert brain.missed_already_reported("p9", at)


def test_khach_bi_ngung_tra_loi_khong_bi_follow_up(monkeypatch, tmp_path):
    """Khách bot đã cố ý ngưng -> vòng follow-up phải bỏ qua, không nhắn nhắc lại."""
    import asyncio
    monkeypatch.setattr(brain, "_HIST_DIR", tmp_path)
    messenger._noise_decision("p7", "o")
    messenger._noise_decision("p7", "ommo")          # -> stopped

    monkeypatch.setattr(brain, "followup_candidates",
                        lambda after_h: [("p7", "2026-07-29 03:00:00")])
    da_gui: list[str] = []

    async def _fake_send(psid, text):
        da_gui.append(psid)

    monkeypatch.setattr(messenger, "send_text", _fake_send)
    asyncio.run(messenger.run_followups())

    assert da_gui == []


def test_khach_tu_choi_hoac_hoan_lai_khong_bi_follow_up(monkeypatch, tmp_path):
    """Ý định 'tu choi' / 'hoan lai' -> loại khỏi danh sách nhắc; 'quan tam' thì vẫn nhắc."""
    import json
    from datetime import datetime, timedelta
    monkeypatch.setattr(brain, "_HIST_DIR", tmp_path)
    im_luc = (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")

    for psid, y in (("tc", "tu choi"), ("hl", "hoan lai"), ("qt", "quan tam")):
        (tmp_path / f"{psid}.json").write_text(json.dumps([
            {"role": "user", "content": "cho xem mau", "at": im_luc},
            {"role": "assistant", "content": "da em gui Bac", "at": im_luc},
        ]), encoding="utf-8")
        (tmp_path / f"{psid}.profile.json").write_text(
            json.dumps({"y_dinh": y}), encoding="utf-8")

    duoc_nhac = {psid for psid, _ in brain.followup_candidates(4.0)}
    assert duoc_nhac == {"qt"}


def test_khach_kho_chiu_thi_bot_im_du_khach_nhan_tin_co_nghia(monkeypatch, tmp_path):
    """Tạm dừng vì khó chịu KHÔNG được tin có nghĩa mở lại - chỉ hết hạn giờ mới mở."""
    import time
    monkeypatch.setattr(brain, "_HIST_DIR", tmp_path)
    reply, ly_do = messenger._extract_pause("Da em xin ghi nhan a. <<TAMDUNG:khach gat vi bi hoi lap>>")
    assert reply == "Da em xin ghi nhan a."
    assert ly_do == "khach gat vi bi hoi lap"

    messenger._pause_bot("pk", ly_do)
    assert messenger._noise_state("pk").get("stopped")          # follow-up + bảng đều lọc cờ này
    assert messenger._noise_decision("pk", "the gia bao nhieu")[0] == "blocked"

    # hết hạn -> khách quay lại được phục vụ bình thường
    state.patch("pk", paused_until=time.time() - 1)
    assert messenger._noise_decision("pk", "the gia bao nhieu")[0] == "allow"
    assert not messenger._noise_state("pk").get("stopped")


def test_tin_khieu_nai_bi_coi_la_kho_chiu_con_xin_gap_nguoi_thi_khong():
    assert messenger._forced_handoff_reason("bên này lừa đảo à") in messenger._LY_DO_KHO_CHIU
    assert messenger._forced_handoff_reason("TU VAN KIEU GI VAY HA") in messenger._LY_DO_KHO_CHIU
    assert messenger._forced_handoff_reason("cho gặp nhân viên") not in messenger._LY_DO_KHO_CHIU
    assert messenger._forced_handoff_reason("mộ đá giá bao nhiêu") is None


def test_noise_guard_keeps_short_valid_replies():
    for text in ("ok", "co", "khong", "0912345678", "M01"):
        assert messenger._is_meaningful_text(text), text


def test_noise_guard_marks_single_character_as_unclear():
    assert not messenger._is_meaningful_text("o")
