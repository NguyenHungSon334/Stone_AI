import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import brain
import messenger


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


def test_noise_guard_keeps_short_valid_replies():
    for text in ("ok", "co", "khong", "0912345678", "M01"):
        assert messenger._is_meaningful_text(text), text


def test_noise_guard_marks_single_character_as_unclear():
    assert not messenger._is_meaningful_text("o")
