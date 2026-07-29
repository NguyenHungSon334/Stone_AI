"""Gộp stats theo ngày: nén 1 ngày thành 1 bản ghi mà dashboard vẫn ra đúng số."""
import json
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fb
import stats
import util


def _ts(ngay_truoc: int, gio: int = 12) -> float:
    d = (datetime.now() - timedelta(days=ngay_truoc)).replace(
        hour=gio, minute=0, second=0, microsecond=0)
    return d.timestamp()


def _bo_su_kien() -> list[dict]:
    """3 ngày trước: 2 lượt ok + 1 lỗi. Hôm nay: 1 lượt ok."""
    return [
        {"ts": _ts(3), "kind": "ok", "psid": "A", "dur": 4.0},
        {"ts": _ts(3), "kind": "usage", "psid": "A", "tin": 1000, "tout": 100},
        {"ts": _ts(3), "kind": "ok", "psid": "B", "dur": 6.0},
        {"ts": _ts(3), "kind": "usage", "psid": "B", "tin": 3000, "tout": 200},
        {"ts": _ts(3), "kind": "error", "psid": "C", "note": "Gemini 503"},
        {"ts": _ts(0), "kind": "ok", "psid": "D", "dur": 2.0},
        {"ts": _ts(0), "kind": "usage", "psid": "D", "tin": 500, "tout": 50},
    ]


def _dung_moi_truong(monkeypatch, tmp_path, rows):
    ev = tmp_path / "events.jsonl"
    ev.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(stats, "_EVENTS", ev)
    monkeypatch.setattr(stats, "_DAILY", tmp_path / "daily.json")
    monkeypatch.setattr(stats, "_STATS_DIR", tmp_path)
    monkeypatch.setattr(fb, "fetch_events", lambda cutoff: None)   # -> đọc file local
    monkeypatch.setattr(fb, "fetch_daily", lambda: None)           # -> đọc daily.json local
    monkeypatch.setattr(fb, "save_daily", lambda gop: True)
    monkeypatch.setattr(fb, "prune_events", lambda cutoff: 0)
    stats._CACHE.update(at=0.0, cutoff=0.0, rows=None)
    return ev


def test_gop_roi_van_ra_dung_so(monkeypatch, tmp_path):
    """Chốt chính: gộp xong, summary/cost_breakdown phải ra Y HỆT lúc còn sự kiện thô."""
    rows = _bo_su_kien()
    _dung_moi_truong(monkeypatch, tmp_path, rows)

    truoc_sum = stats.summary(7)
    truoc_cost = stats.cost_breakdown(30)

    stats.prune(keep_days=1)             # ngày -3 bị gộp, hôm nay giữ nguyên
    stats._CACHE.update(at=0.0, cutoff=0.0, rows=None)

    sau_sum = stats.summary(7)
    sau_cost = stats.cost_breakdown(30)

    for k in ("tokens_in", "tokens_out", "cost_usd", "counts", "total_handled",
              "success_rate", "avg_reply_s", "max_reply_s", "daily"):
        assert sau_sum[k] == truoc_sum[k], k
    for k in ("today_usd", "d7_usd", "period_usd", "replies", "daily"):
        assert sau_cost[k] == truoc_cost[k], k


def test_gop_xong_thi_su_kien_tho_bien_mat(monkeypatch, tmp_path):
    ev = _dung_moi_truong(monkeypatch, tmp_path, _bo_su_kien())

    kq = stats.prune(keep_days=1)

    con = [json.loads(l) for l in ev.read_text(encoding="utf-8").splitlines()]
    assert len(con) == 2 and {r["psid"] for r in con} == {"D"}       # chỉ còn hôm nay
    gop = util.read_json(tmp_path / "daily.json", {})
    ngay3 = (datetime.now() - timedelta(days=3)).date().isoformat()
    assert kq["ngay_da_gop"] == [ngay3]
    assert gop[ngay3] == {"date": ngay3, "tin": 4000, "tout": 300, "ok": 2, "error": 1,
                          "handoff": 0, "rate_limited": 0, "dur_sum": 10.0, "dur_n": 2,
                          "dur_max": 6.0, "psids": 3}


def test_chay_prune_nhieu_lan_khong_cong_doi(monkeypatch, tmp_path):
    """Mốc cắt theo nửa đêm -> ngày đã gộp là chung cuộc, ghi đè được, chạy lại vẫn thế."""
    _dung_moi_truong(monkeypatch, tmp_path, _bo_su_kien())
    stats.prune(keep_days=1)
    lan1 = stats.summary(7)

    for _ in range(3):
        stats.prune(keep_days=1)
        stats._CACHE.update(at=0.0, cutoff=0.0, rows=None)

    assert stats.summary(7) == lan1


def test_ngay_con_su_kien_tho_khong_bi_dem_hai_lan(monkeypatch, tmp_path):
    """Bản gộp và sự kiện thô cùng tồn tại cho 1 ngày -> chỉ được tính sự kiện thô."""
    rows = _bo_su_kien()
    _dung_moi_truong(monkeypatch, tmp_path, rows)
    hom_nay = datetime.now().date().isoformat()
    # Cố tình ghi bản gộp cho HÔM NAY trong khi sự kiện thô hôm nay vẫn còn nguyên.
    util.write_json_atomic(tmp_path / "daily.json", {
        hom_nay: {"date": hom_nay, "tin": 999999, "tout": 999999, "ok": 99, "error": 0,
                  "handoff": 0, "rate_limited": 0, "dur_sum": 0.0, "dur_n": 0,
                  "dur_max": 0.0, "psids": 0}})

    s = stats.summary(7)

    assert s["tokens_in"] == 4500 and s["counts"]["ok"] == 3    # đúng số thô, không cộng bản gộp
