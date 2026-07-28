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


def test_noise_guard_keeps_short_valid_replies():
    for text in ("ok", "co", "khong", "0912345678", "M01"):
        assert messenger._is_meaningful_text(text), text


def test_noise_guard_marks_single_character_as_unclear():
    assert not messenger._is_meaningful_text("o")
