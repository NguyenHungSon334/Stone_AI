import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import brain
import util


def test_ho_so_khach_con_trong_prompt_sau_khi_lich_su_dai(monkeypatch, tmp_path):
    monkeypatch.setattr(brain, "_HIST_DIR", tmp_path)
    psid = "khach-1"
    hist = [{"role": "user", "content": "tin nhan"} for _ in range(30)]
    profile = {
        "ten": "Anh Nam", "sdt": "0912345678", "nhu_cau": "Khu lang gia toc",
        "hang_muc": "Long dinh", "so_luong": "8 ngoi", "vat_lieu": "da xanh reu",
        "dia_chi": "Ha Tinh", "tinh": "Ha Tinh", "khu_vuc": "Mien Trung",
        "xe_cau": "xe vao tan noi", "thoi_gian": "thang 9/2026",
        "ghi_chu": "Can bao gia", "upto": len(hist),
    }
    util.write_json_atomic(brain._profile_path(psid), profile)

    restored = brain._profile_from_history_sync(psid, hist)
    prompt = brain._profile_prompt(restored)

    assert "SĐT/Zalo: 0912345678" in prompt
    assert "Hạng mục: Long dinh" in prompt
    assert "Thời gian: thang 9/2026" in prompt
